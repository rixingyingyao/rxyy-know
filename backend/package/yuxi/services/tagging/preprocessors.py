"""
多模态预处理器

将不同类型的文件（音频/图片/视频/文本）预处理为统一的文本表示，
供知识库入库和 LLM 打标共用。

音频双策略：
- ≤30min 且文件不大: qwen3.5-omni-plus 直接理解（语气/情感/背景音）
- 其他: ASR 转写——优先 filetrans 异步文件转写（公网 URL），回退 Recognition 本地识别

音视频输出带时间戳的原文转写（而非仅模型摘要），保证检索可命中原话、引用可定位时间点。
"""

import asyncio
import base64
import ipaddress
import json
import mimetypes
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from tenacity import retry, stop_after_attempt, wait_exponential

from yuxi.utils import logger

# 文件大小限制：2GB
_MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024


@dataclass
class PreprocessResult:
    """预处理结果"""

    text: str = ""
    images: list[str] = field(default_factory=list)  # base64 images
    metadata: dict = field(default_factory=dict)
    source_type: str = ""  # audio / image / video / text
    model_used: str = ""
    transcript: str = ""  # 带时间戳的原文转写（音视频；空 = 无逐句转写）


def _get_audio_duration(file_path: str) -> float:
    """
    用 ffprobe 获取音视频时长（秒）。

    探测失败时按文件大小粗估（约 1MB/min，128kbps 音频标准）而非返回魔数：
    高码率文件会被高估时长从而走 ASR 长音频链路——方向保守且正确
    （大文件本就不适合 base64 直传 Omni）。
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception as e:
        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
        except OSError:
            size_mb = 0.0
        estimated = size_mb * 60.0
        logger.warning(
            f"ffprobe failed for {file_path}, estimating duration from size "
            f"({size_mb:.1f}MB -> ~{estimated / 60:.0f}min): {e}"
        )
        return estimated


def _get_mime_type(file_path: str) -> str:
    """获取文件 MIME 类型"""
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"


def _file_to_base64(file_data: bytes) -> str:
    """文件数据转 base64"""
    return base64.b64encode(file_data).decode("utf-8")


def _format_ms(ms: float | int | None) -> str:
    """毫秒 → MM:SS 或 HH:MM:SS"""
    if ms is None:
        return "?"
    total_seconds = int(ms) // 1000
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _sentences_to_transcript(sentences: list[dict]) -> str:
    """ASR 句子列表 → 带时间戳的逐句转写文本"""
    lines = []
    for s in sentences:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        begin = _format_ms(s.get("begin_time"))
        end = _format_ms(s.get("end_time"))
        lines.append(f"[{begin}-{end}] {text}")
    return "\n".join(lines)


def _is_public_http_url(url: str | None) -> bool:
    """判断 URL 是否可能被云端服务（DashScope filetrans）访问：公网域名或公网 IP"""
    if not url or not url.startswith(("http://", "https://")):
        return False
    host = urlparse(url).hostname
    if not host or host.lower() in ("localhost",):
        return False
    try:
        return not ipaddress.ip_address(host).is_private
    except ValueError:
        # 非 IP（域名）→ 视为公网可达，由调用方失败回退兜底
        return True


class DashScopeASRClient:
    """
    DashScope ASR 客户端 — 双链路

    1. filetrans 异步文件转写（默认 qwen3-asr-flash-filetrans）：源文件有公网可达
       URL 时优先使用，长音频更快更准、带句级时间戳
    2. Recognition 本地识别（paraformer-realtime-v1）：filetrans 不可用/失败时回退

    两条链路统一返回句子列表 [{begin_time, end_time, text}]（毫秒）。
    """

    RECOGNITION_MODEL = "paraformer-realtime-v1"

    def __init__(self, api_key: str, filetrans_model: str = "qwen3-asr-flash-filetrans"):
        self.api_key = api_key
        self.filetrans_model = filetrans_model

    async def transcribe(self, file_data: bytes, filename: str) -> str:
        """将音频数据转写为纯文本（兼容旧接口）"""
        sentences = await self.transcribe_sentences(file_data, filename)
        return "".join(s.get("text", "") for s in sentences)

    async def transcribe_sentences(self, file_data: bytes, filename: str) -> list[dict]:
        """音频 bytes → 句子列表（写临时文件后走路径链路）"""
        suffix = Path(filename).suffix.lower() or ".wav"
        tmp_input = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp_input.write(file_data)
            tmp_input.close()
            return await self.transcribe_path_sentences(tmp_input.name)
        finally:
            Path(tmp_input.name).unlink(missing_ok=True)

    async def transcribe_path_sentences(self, file_path: str, source_url: str | None = None) -> list[dict]:
        """
        音频文件路径 → 句子列表

        Args:
            file_path: 本地音频文件路径
            source_url: 原始文件的 HTTP URL（公网可达时优先走 filetrans）
        """
        if self.filetrans_model and _is_public_http_url(source_url):
            try:
                return await asyncio.to_thread(self._filetrans_sync, source_url)
            except Exception as e:
                logger.warning(f"filetrans ASR failed, falling back to local Recognition: {e}")

        return await self._recognize_local(file_path)

    async def _recognize_local(self, file_path: str) -> list[dict]:
        """本地文件 → 16kHz mono WAV → Recognition SDK"""
        tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_wav.close()
        try:
            convert_cmd = [
                "ffmpeg", "-y", "-i", file_path,
                "-ar", "16000", "-ac", "1", "-f", "wav",
                tmp_wav.name,
            ]
            proc = await asyncio.create_subprocess_exec(
                *convert_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", errors="replace")
                logger.error(f"ffmpeg convert failed: {err[:300]}")
                raise RuntimeError(f"Audio conversion failed: {err[:200]}")

            return await asyncio.to_thread(self._recognize_sync, tmp_wav.name)
        finally:
            Path(tmp_wav.name).unlink(missing_ok=True)

    def _ensure_api_key_env(self) -> None:
        # 仅在主线程未设置时补充（进程级一次性操作，非线程内反复修改）
        if "DASHSCOPE_API_KEY" not in os.environ and self.api_key:
            os.environ["DASHSCOPE_API_KEY"] = self.api_key

    def _filetrans_sync(self, file_url: str) -> list[dict]:
        """DashScope 异步文件转写：提交任务 → 轮询 → 下载转写 JSON → 句子列表"""
        self._ensure_api_key_env()

        from dashscope.audio.asr import Transcription

        task = Transcription.async_call(model=self.filetrans_model, file_urls=[file_url])
        task_id = task.output.get("task_id") if isinstance(task.output, dict) else getattr(task.output, "task_id", None)
        if not task_id:
            raise RuntimeError(f"filetrans submit failed: {getattr(task, 'message', task)}")

        rsp = Transcription.wait(task=task_id)
        if rsp.status_code != 200:
            raise RuntimeError(f"filetrans failed ({rsp.status_code}): {getattr(rsp, 'message', rsp)}")

        output = rsp.output if isinstance(rsp.output, dict) else {}
        if output.get("task_status") != "SUCCEEDED":
            raise RuntimeError(f"filetrans task not succeeded: {output.get('task_status')}")

        results = output.get("results") or []
        if not results or results[0].get("subtask_status") not in (None, "SUCCEEDED"):
            raise RuntimeError(f"filetrans subtask failed: {results}")

        transcription_url = results[0].get("transcription_url")
        if not transcription_url:
            raise RuntimeError("filetrans result missing transcription_url")

        import urllib.request

        with urllib.request.urlopen(transcription_url, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        sentences: list[dict] = []
        for transcript in payload.get("transcripts", []):
            for s in transcript.get("sentences", []):
                sentences.append({
                    "begin_time": s.get("begin_time"),
                    "end_time": s.get("end_time"),
                    "text": s.get("text", ""),
                })
        if not sentences:
            logger.warning(f"filetrans returned no sentences for {file_url}")
        return sentences

    def _recognize_sync(self, file_path: str) -> list[dict]:
        """同步调用 Recognition SDK，返回句子列表"""
        self._ensure_api_key_env()

        from dashscope.audio.asr import Recognition

        rec = Recognition(
            model=self.RECOGNITION_MODEL,
            format=Path(file_path).suffix.lstrip("."),
            sample_rate=16000,
            callback=None,
        )
        result = rec.call(file=file_path)

        if hasattr(result, "status_code") and result.status_code != 200:
            code = getattr(result, "code", "Unknown")
            message = getattr(result, "message", str(result))
            raise RuntimeError(f"ASR failed ({code}): {message}")

        # 提取识别结果
        sentences = result.get_sentence() if hasattr(result, "get_sentence") else None
        if not sentences and hasattr(result, "output") and result.output:
            sentences = result.output.get("sentence", [])

        if not sentences:
            logger.warning(f"ASR returned no sentences for {file_path}")
            return []

        return [
            {
                "begin_time": s.get("begin_time"),
                "end_time": s.get("end_time"),
                "text": s.get("text", ""),
            }
            for s in sentences
        ]


_MUSIC_KEYWORDS = {"音乐", "歌曲", "旋律", "乐器", "伴奏", "纯音乐", "器乐", "曲风", "节拍", "和弦", "演唱", "歌词", "副歌", "曲调"}


def _is_music_content(text: str, strategy: str) -> bool:
    """
    判断音频内容是否为音乐（而非语音/新闻/对话等）

    收紧判定避免误判：报道音乐活动的新闻通常文本长但关键词稀疏，
    而 Omni 对纯音乐的描述短且关键词密集。
    """
    stripped = text.strip()
    if strategy in ("asr", "asr_fallback") and len(stripped) < 20:
        return True  # ASR 几乎无语音 → 纯音乐
    hits = sum(1 for kw in _MUSIC_KEYWORDS if kw in text)
    return hits >= 4 or (hits >= 2 and len(stripped) < 200)


class AudioPreprocessor:
    """
    音频预处理器 — 双策略

    ≤30min 且文件 ≤ omni_max_mb: Omni 直接理解（qwen3.5-omni-plus），失败降级 ASR
    其他: ASR 转写（filetrans 优先，Recognition 兜底），输出带时间戳的逐句原文
    """

    def __init__(self, config: dict):
        self.threshold_minutes = config.get("audio_strategy_threshold_minutes", 30)
        self.audio_model = config.get("audio_model", "alibaba:qwen3.5-omni-plus")
        self.asr_model = config.get("asr_model", "qwen3-asr-flash-filetrans")
        self.audio_prompt = config.get("audio_transcribe_prompt", "请描述这段音频的主要内容，包括主题、关键信息和情感基调。")
        self.long_audio_segments = config.get("long_audio_summary_segments", 10)
        # Omni 走 base64 直传，过大的文件编码内存翻倍且易超模型限制 → 直接走 ASR
        self.omni_max_mb = config.get("audio_omni_max_mb", 50)

    async def process(self, file_data: bytes, filename: str) -> PreprocessResult:
        """处理音频 bytes（兼容入口：写临时文件后走路径链路）"""
        suffix = Path(filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name
        try:
            return await self.process_path(tmp_path, filename)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    async def process_path(self, file_path: str, filename: str, source_url: str | None = None) -> PreprocessResult:
        """
        处理音频文件（路径直传，避免全量读内存）

        Args:
            file_path: 本地音频文件路径
            filename: 原始文件名（用于推断格式）
            source_url: 原始文件 HTTP URL（公网可达时 ASR 走 filetrans）
        """
        duration = _get_audio_duration(file_path)
        duration_min = duration / 60
        try:
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        except OSError:
            file_size_mb = 0.0

        suffix = Path(filename).suffix or Path(file_path).suffix or ".wav"
        use_omni = duration_min <= self.threshold_minutes and file_size_mb <= self.omni_max_mb

        transcript = ""
        if use_omni:
            try:
                text = await self._omni_understand_path(file_path, filename, suffix)
                model_used = self.audio_model
                strategy = "omni"
            except Exception as e:
                logger.warning(f"Omni model failed, falling back to ASR: {e}")
                text, transcript = await self._asr_transcribe_path(file_path, source_url)
                model_used = self.asr_model
                strategy = "asr_fallback"
        else:
            text, transcript = await self._asr_transcribe_path(file_path, source_url)
            model_used = self.asr_model
            strategy = "asr"

        return PreprocessResult(
            text=text,
            transcript=transcript,
            source_type="music" if _is_music_content(transcript or text, strategy) else "audio",
            model_used=model_used,
            metadata={"duration_seconds": duration, "strategy": strategy},
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    async def _omni_understand_path(self, file_path: str, filename: str, suffix: str) -> str:
        """Omni 模型直接理解音频（带重试；读文件仅发生在确定走 Omni 后）"""
        from yuxi.models import select_model

        model = select_model(model_spec=self.audio_model)
        file_data = await asyncio.to_thread(Path(file_path).read_bytes)
        audio_b64 = _file_to_base64(file_data)

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        # DashScope 兼容模式要求 data URI 形式，裸 base64 会被当 URL 解析报 InvalidParameter
                        "input_audio": {"data": f"data:;base64,{audio_b64}", "format": suffix.lstrip(".")},
                    },
                    {"type": "text", "text": self.audio_prompt},
                ],
            }
        ]

        response = await model.call(messages)
        return response.content if hasattr(response, "content") else str(response)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    async def _asr_transcribe_path(self, file_path: str, source_url: str | None = None) -> tuple[str, str]:
        """
        ASR 转写（带重试）

        Returns:
            (text, transcript): text = 入库用 markdown（带时间戳逐句原文），
            transcript = 纯逐句转写（供视频链路合并使用）
        """
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY not set")

        client = DashScopeASRClient(api_key, filetrans_model=self.asr_model)
        sentences = await client.transcribe_path_sentences(file_path, source_url)

        transcript = _sentences_to_transcript(sentences)
        if transcript:
            text = f"## 音频转写（带时间戳）\n\n{transcript}"
        else:
            # 无时间戳信息时退化为纯文本拼接
            plain = "".join(s.get("text", "") for s in sentences)
            text = plain
            transcript = plain
        return text, transcript


class ImagePreprocessor:
    """图片预处理器 — VL 模型理解"""

    def __init__(self, config: dict):
        self.vl_model = config.get("vl_model", "alibaba:qwen3.7-plus")
        self.image_prompt = config.get("image_describe_prompt", "请详细描述这张图片的主要内容、场景、人物和关键信息。")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    async def process(self, file_data: bytes, filename: str) -> PreprocessResult:
        """处理图片文件（带重试）"""
        from yuxi.models import select_model

        model = select_model(model_spec=self.vl_model)
        img_b64 = _file_to_base64(file_data)
        mime = _get_mime_type(filename)

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                    },
                    {"type": "text", "text": self.image_prompt},
                ],
            }
        ]

        response = await model.call(messages)
        text = response.content if hasattr(response, "content") else str(response)

        return PreprocessResult(
            text=text,
            source_type="image",
            model_used=self.vl_model,
        )


class VideoPreprocessor:
    """
    视频预处理器

    ffmpeg 分离音轨 + 场景检测抽帧（失败回退等间隔）→ 分别处理后合并。
    输出结构：内容摘要（LLM）+ 音频转写原文（带时间戳）+ 画面描述（带时间区间），
    保证检索能命中台词原话、引用能定位时间点。
    """

    def __init__(self, config: dict):
        self.frame_interval = config.get("video_frame_interval_seconds", 30)
        self.max_frames = config.get("video_max_frames", 10)
        self.frames_per_batch = config.get("video_frames_per_batch", 5)
        self.scene_threshold = config.get("video_scene_threshold", 0.3)
        self.video_prompt = config.get(
            "video_summary_prompt",
            "根据以下视频的画面描述和音频转写内容，生成一份详细的内容摘要。"
            "要求：\n"
            "1. **音频内容是核心**：优先提取音频中的具体话题、人物、事件、观点、数据等信息\n"
            "2. **画面作为补充**：描述关键场景、人物画面、字幕信息\n"
            "3. 摘要应该保留具体细节（人名、地名、事件名、数字等），不要泛泛而谈\n"
            "4. 分段输出：先输出音频核心内容，再输出视觉关键信息",
        )
        self.audio_preprocessor = AudioPreprocessor(config)
        self.image_preprocessor = ImagePreprocessor(config)

    async def process(self, file_data: bytes, filename: str) -> PreprocessResult:
        """处理视频 bytes（兼容入口：写临时文件后走路径链路）"""
        if len(file_data) > _MAX_FILE_SIZE:
            raise ValueError(f"视频文件过大 ({len(file_data) / 1024 / 1024:.0f}MB)，上限 {_MAX_FILE_SIZE // 1024 // 1024}MB")
        suffix = Path(filename).suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_data)
            video_path = tmp.name
        try:
            return await self.process_path(video_path, filename)
        finally:
            Path(video_path).unlink(missing_ok=True)

    async def process_path(self, video_path: str, filename: str, source_url: str | None = None) -> PreprocessResult:
        """处理视频文件（路径直传，避免全量读内存）"""
        try:
            file_size = os.path.getsize(video_path)
        except OSError:
            file_size = 0
        if file_size > _MAX_FILE_SIZE:
            raise ValueError(f"视频文件过大 ({file_size / 1024 / 1024:.0f}MB)，上限 {_MAX_FILE_SIZE // 1024 // 1024}MB")

        audio_result, frame_texts = await asyncio.gather(
            self._extract_and_process_audio(video_path, filename),
            self._extract_and_process_frames(video_path),
        )

        # ASR 链路有逐句时间戳转写；Omni 链路（短音轨）只有理解性文本
        transcript = audio_result.transcript
        audio_block = transcript or audio_result.text
        audio_title = "## 音频转写（带时间戳）" if transcript else "## 音频内容"
        frames_block = "\n".join(frame_texts)

        # LLM 摘要（基于转写 + 画面描述）
        summary = ""
        combined_for_summary = ""
        if audio_block:
            combined_for_summary += f"【音频内容】\n{audio_block}\n\n"
        if frames_block:
            combined_for_summary += f"【画面描述】\n{frames_block}"

        if combined_for_summary:
            try:
                summary = await self._summarize(combined_for_summary)
            except Exception as e:
                logger.warning(f"Video summary LLM call failed, keep raw transcript: {e}")

        # 摘要头 + 原文转写 + 画面描述：摘要便于人读，原文保证检索命中与时间定位
        parts = []
        if summary:
            parts.append(f"## 内容摘要\n\n{summary}")
        if audio_block:
            parts.append(f"{audio_title}\n\n{audio_block}")
        if frames_block:
            parts.append(f"## 画面描述\n\n{frames_block}")
        text = "\n\n".join(parts)

        return PreprocessResult(
            text=text,
            transcript=transcript or audio_result.text,
            source_type="video",
            model_used="multi-model",
            metadata={
                "frames_processed": len(frame_texts),
                "audio_strategy": audio_result.metadata.get("strategy", ""),
                "duration_seconds": audio_result.metadata.get("duration_seconds", 0),
            },
        )

    async def _summarize(self, combined: str) -> str:
        """用 LLM 生成内容摘要"""
        from yuxi import config as app_config
        from yuxi.models import select_model
        from yuxi.services.tagging.prompt_config import get_prompt_config

        cfg = get_prompt_config()
        tag_model = cfg.get("models", {}).get("tag_model", "") or None
        model_spec = tag_model or app_config.fast_model
        model = select_model(model_spec=model_spec)

        messages = [
            {"role": "user", "content": f"{self.video_prompt}\n\n{combined}"},
        ]
        response = await model.call(messages)
        return response.content if hasattr(response, "content") else str(response)

    async def _extract_and_process_audio(self, video_path: str, filename: str) -> PreprocessResult:
        """从视频提取音轨并处理，返回完整 PreprocessResult（含 transcript）"""
        audio_path = video_path + ".wav"
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1", audio_path, "-y",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.warning("Failed to extract audio from video")
                return PreprocessResult(source_type="audio")

            return await self.audio_preprocessor.process_path(audio_path, f"{filename}.wav")
        except Exception as e:
            logger.warning(f"Video audio extraction failed: {e}")
            return PreprocessResult(source_type="audio")
        finally:
            Path(audio_path).unlink(missing_ok=True)

    def _target_frame_count(self, duration: float) -> int:
        """自适应帧数：短视频 3-5 帧，长视频约每分钟 1 帧，上限 max_frames"""
        if duration <= 60:
            return 3
        if duration <= 300:
            return 5
        return max(5, min(int(duration / 60), self.max_frames))

    async def _extract_frames_scene(self, video_path: str, tmpdir: str, max_frames: int) -> list[tuple[Path, float]]:
        """
        场景检测抽帧：select scene filter 抓取画面突变帧，showinfo 解析帧时间戳。

        Returns:
            [(frame_path, timestamp_seconds)]；失败或帧数过少返回空列表（调用方回退等间隔）
        """
        import re as _re

        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", video_path,
                "-vf", f"select='gt(scene,{self.scene_threshold})',showinfo",
                "-vsync", "vfr",
                "-frames:v", str(max_frames),
                "-q:v", "2",
                f"{tmpdir}/scene_%04d.jpg", "-y",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                return []

            frame_files = sorted(Path(tmpdir).glob("scene_*.jpg"))
            if len(frame_files) < 3:
                return []  # 场景变化太少，等间隔覆盖更均匀

            # showinfo 每输出一帧打一行 pts_time，顺序与帧文件序号一致
            stderr_text = (stderr or b"").decode("utf-8", errors="replace")
            pts_times = [float(m) for m in _re.findall(r"pts_time:([0-9]+\.?[0-9]*)", stderr_text)]

            result = []
            for i, fp in enumerate(frame_files):
                ts = pts_times[i] if i < len(pts_times) else -1.0
                result.append((fp, ts))
            return result
        except Exception as e:
            logger.warning(f"Scene-detection frame extraction failed: {e}")
            return []

    async def _extract_frames_uniform(self, video_path: str, tmpdir: str, duration: float, target_frames: int) -> list[tuple[Path, float]]:
        """等间隔抽帧（场景检测的回退方案）"""
        interval = max(1.0, duration / target_frames)
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", video_path,
                "-vf", f"fps=1/{interval}",
                "-frames:v", str(target_frames),
                "-q:v", "2",
                f"{tmpdir}/frame_%04d.jpg", "-y",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            logger.warning(f"Frame extraction failed: {e}")
            return []

        frame_files = sorted(Path(tmpdir).glob("frame_*.jpg"))
        return [(fp, i * interval) for i, fp in enumerate(frame_files)]

    async def _extract_and_process_frames(self, video_path: str) -> list[str]:
        """从视频抽帧（场景检测优先）并用 VL 模型分批识别（带历史记忆）"""
        duration = _get_audio_duration(video_path)
        if duration <= 0:
            return []

        target_frames = self._target_frame_count(duration)

        with tempfile.TemporaryDirectory() as tmpdir:
            frames = await self._extract_frames_scene(video_path, tmpdir, target_frames)
            frame_mode = "scene"
            if not frames:
                frames = await self._extract_frames_uniform(video_path, tmpdir, duration, target_frames)
                frame_mode = "uniform"
            if not frames:
                return []

            logger.info(f"Video frames extracted: {len(frames)} ({frame_mode} mode)")

            from yuxi.models import select_model
            model = select_model(model_spec=self.image_preprocessor.vl_model)

            # 分批处理，每批带上前面批次的摘要作为上下文
            batch_size = self.frames_per_batch
            batches = [frames[i:i + batch_size] for i in range(0, len(frames), batch_size)]
            all_descriptions = []
            running_summary = ""

            for batch_idx, batch in enumerate(batches):
                content_parts = []
                ts_list = []

                # 构建多图内容
                for fp, ts in batch:
                    data = fp.read_bytes()
                    img_b64 = _file_to_base64(data)
                    mime = _get_mime_type(fp.name)
                    ts_list.append(ts)
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                    })

                # 帧时间点（场景检测的帧非均匀分布，用真实时间戳）
                known_ts = [t for t in ts_list if t >= 0]
                time_start = int(min(known_ts)) if known_ts else 0
                time_end = int(max(known_ts)) if known_ts else 0
                ts_label = "、".join(f"{int(t)}s" for t in known_ts) if known_ts else "未知"

                if running_summary:
                    prompt = (
                        f"这是一段视频中 {time_start}s~{time_end}s 的 {len(batch)} 帧画面（时间点：{ts_label}）。\n"
                        f"【前段内容回顾】{running_summary}\n\n"
                        "请在前段内容的基础上，描述当前画面中新出现的场景、人物行为和关键信息的变化。"
                    )
                else:
                    prompt = (
                        f"以下是一段视频 {time_start}s~{time_end}s 的 {len(batch)} 帧画面（时间点：{ts_label}）。"
                        "请描述画面中的场景、人物、行为和关键信息。"
                    )

                content_parts.append({"type": "text", "text": prompt})
                messages = [{"role": "user", "content": content_parts}]

                try:
                    response = await model.call(messages)
                    text = response.content if hasattr(response, "content") else str(response)
                    if text:
                        all_descriptions.append(f"[{time_start}s-{time_end}s] {text}")
                        # 更新滚动摘要（截取最近 200 字避免上下文过长）
                        running_summary = text[-200:] if len(text) > 200 else text
                except Exception as e:
                    logger.warning(f"Batch {batch_idx + 1} VL call failed: {e}")

            return all_descriptions


class TextPreprocessor:
    """文本预处理器 — 直接透传"""

    async def process(self, text: str, filename: str = "") -> PreprocessResult:
        return PreprocessResult(
            text=text,
            source_type="text",
            model_used="none",
        )


# MIME type → preprocessor 类型映射
MIME_TYPE_MAP: dict[str, str] = {
    "audio": "audio",
    "image": "image",
    "video": "video",
    "text": "text",
    "application/pdf": "text",
    "application/vnd": "text",
}


def detect_preprocessor_type(mime_type: str) -> str:
    """根据 MIME 类型确定预处理器类型"""
    if not mime_type:
        return "text"
    main_type = mime_type.split("/")[0]
    if main_type in MIME_TYPE_MAP:
        return MIME_TYPE_MAP[main_type]
    if mime_type in MIME_TYPE_MAP:
        return MIME_TYPE_MAP[mime_type]
    return "text"
