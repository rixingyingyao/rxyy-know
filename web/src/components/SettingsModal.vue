<template>
  <a-modal
    v-model:open="visible"
    :title="null"
    width="90%"
    :style="{ maxWidth: '980px', minWidth: '320px', top: '10%' }"
    :footer="null"
    :closable="false"
    @cancel="handleClose"
    class="settings-modal"
    :destroyOnClose="true"
    :bodyStyle="{ padding: 0 }"
  >
    <div class="settings-container">
      <button class="settings-close-btn lucide-icon-btn" @click="handleClose" aria-label="关闭设置">
        <X :size="16" />
      </button>

      <!-- 侧边栏 (Desktop) -->
      <div class="settings-sider">
        <div class="settings-sider-nav">
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'account' }"
            @click="activeTab = 'account'"
            v-if="userStore.isLoggedIn"
          >
            <CircleUser class="icon" :size="18" />
            <span>账户设置</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'userConfig' }"
            @click="activeTab = 'userConfig'"
            v-if="userStore.isLoggedIn"
          >
            <SlidersHorizontal class="icon" :size="18" />
            <span>用户配置</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'base' }"
            @click="activeTab = 'base'"
            v-if="userStore.isAdmin"
          >
            <Settings class="icon" :size="18" />
            <span>基本设置</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'user' }"
            @click="activeTab = 'user'"
            v-if="userStore.isAdmin"
          >
            <User class="icon" :size="18" />
            <span>用户管理</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'department' }"
            @click="activeTab = 'department'"
            v-if="userStore.isSuperAdmin"
          >
            <Users class="icon" :size="18" />
            <span>部门管理</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'agentEnv' }"
            @click="activeTab = 'agentEnv'"
            v-if="userStore.isLoggedIn"
          >
            <SquareTerminal class="icon" :size="18" />
            <span>环境变量</span>
          </div>
        </div>

      </div>

      <!-- 顶部导航 (Mobile) -->
      <div class="settings-mobile-nav">
        <div
          class="nav-item"
          :class="{ active: activeTab === 'account' }"
          @click="activeTab = 'account'"
          v-if="userStore.isLoggedIn"
        >
          账户设置
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'userConfig' }"
          @click="activeTab = 'userConfig'"
          v-if="userStore.isLoggedIn"
        >
          用户配置
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'agentEnv' }"
          @click="activeTab = 'agentEnv'"
          v-if="userStore.isLoggedIn"
        >
          沙盒环境变量
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'base' }"
          @click="activeTab = 'base'"
          v-if="userStore.isAdmin"
        >
          基本设置
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'user' }"
          @click="activeTab = 'user'"
          v-if="userStore.isAdmin"
        >
          用户管理
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'department' }"
          @click="activeTab = 'department'"
          v-if="userStore.isSuperAdmin"
        >
          部门管理
        </div>
      </div>

      <!-- 内容区域 -->
      <div class="settings-content-wrapper">
        <div class="settings-content">
          <div v-show="activeTab === 'account'" v-if="userStore.isLoggedIn">
            <AccountSettingsComponent />
          </div>

          <div v-if="activeTab === 'userConfig' && userStore.isLoggedIn">
            <UserConfigSettingsCard />
          </div>

          <div v-if="activeTab === 'agentEnv' && userStore.isLoggedIn">
            <AgentEnvSettingsCard />
          </div>

          <div v-show="activeTab === 'base'" v-if="userStore.isAdmin">
            <BasicSettingsSection />
          </div>

          <div v-show="activeTab === 'user'" v-if="userStore.isAdmin">
            <UserManagementComponent />
          </div>

          <div v-show="activeTab === 'department'" v-if="userStore.isSuperAdmin">
            <DepartmentManagementComponent />
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useUserStore } from '@/stores/user'
import {
  CircleUser,
  Settings,
  SlidersHorizontal,
  SquareTerminal,
  User,
  Users,
  X
} from 'lucide-vue-next'
import AccountSettingsComponent from '@/components/AccountSettingsComponent.vue'
import AgentEnvSettingsCard from '@/components/AgentEnvSettingsCard.vue'
import BasicSettingsSection from '@/components/BasicSettingsSection.vue'
import UserConfigSettingsCard from '@/components/UserConfigSettingsCard.vue'
import UserManagementComponent from '@/components/UserManagementComponent.vue'
import DepartmentManagementComponent from '@/components/DepartmentManagementComponent.vue'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  initialTab: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['update:visible', 'close'])

const userStore = useUserStore()
const activeTab = ref('account')

const visible = computed({
  get: () => props.visible,
  set: (value) => emit('update:visible', value)
})

const availableTabs = computed(() => {
  const tabs = []
  if (userStore.isLoggedIn) tabs.push('account', 'userConfig', 'agentEnv')
  if (userStore.isAdmin) tabs.push('base', 'user')
  if (userStore.isSuperAdmin) tabs.push('department')
  return tabs
})

const setActiveTab = (preferredTab) => {
  if (preferredTab && availableTabs.value.includes(preferredTab)) {
    activeTab.value = preferredTab
    return
  }
  activeTab.value = userStore.isAdmin ? 'base' : availableTabs.value[0]
}

const handleClose = () => {
  emit('close')
}

watch(
  () => [props.visible, props.initialTab],
  ([newVal]) => {
    if (newVal) {
      setActiveTab(props.initialTab)
    }
  }
)
</script>

<style lang="less">
.settings-modal.ant-modal {
  .ant-modal-content {
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    position: relative;
    padding: 0;
    overflow: hidden;
  }

  .ant-modal-body {
    padding: 0;
  }
}

.settings-container {
  display: flex;
  height: 70vh;
  width: 100%;
  position: relative;

  @media (max-width: 900px) {
    flex-direction: column;
    height: auto;
    min-height: 70vh;
  }
}

.settings-close-btn {
  position: absolute;
  top: 10px;
  left: 14px;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: var(--gray-50);
  color: var(--gray-700);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 2;

  &:hover {
    background: var(--gray-200);
    color: var(--gray-900);
  }
}

/* Sidebar Styles - Matching SettingView.vue style */
.settings-sider {
  width: 176px;
  height: 100%;
  padding: 52px 10px 12px;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  background: var(--gray-50);
  border-right: 1px solid var(--gray-150);

  @media (max-width: 900px) {
    display: none;
  }

  .settings-sider-nav {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 100%;
  }

  .sider-item {
    width: 100%;
    padding: 6px 12px; /* Matches SettingView .sider > * */
    cursor: pointer;
    transition: all 0.1s; /* Matches SettingView */
    text-align: left;
    font-size: 15px; /* Matches SettingView */
    border-radius: 8px;
    color: var(--gray-700);
    display: flex;
    align-items: center;
    gap: 10px;

    .icon {
      font-size: 14px; /* Slightly adjusted to align better, SettingView uses h() icon defaults */
    }

    &:hover {
      background: var(--gray-50);
    }

    &.activesec {
      background: var(--gray-150);
      color: var(--main-700);
    }
  }

}

/* Content Area */
.settings-content-wrapper {
  flex: 1;
  height: 100%;
  max-width: calc(100% - 176px);
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
  padding: 0;

  @media (max-width: 900px) {
    max-width: 100%;
    padding: 8px;
  }

  .settings-content {
    padding: 16px 16px; /* Keep inner readability without outer panel padding */
    // margin-bottom: 40px; /* Matches SettingView .setting margin-bottom */
    overflow-y: scroll;
    height: auto;
    flex: 1;
    min-height: 0;

    .model-providers-section,
    .user-management,
    .department-management,
    .apikey-management {
      min-height: auto;
    }

    .header-section {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 16px;
      margin-bottom: 16px;
    }

    .header-content {
      flex: 1;
      min-width: 0;
    }

    .section-title {
      font-size: 16px;
      font-weight: 500;
      color: var(--gray-900);
      line-height: 1.4;
      margin: 12px 0 12px;
    }

    .section-description {
      font-size: 14px;
      color: var(--gray-600);
      line-height: 1.4;
      margin: 0;
    }

    .section-subtitle {
      margin: 0;
      font-size: 16px;
      font-weight: 500;
      color: var(--gray-900);
    }

    .add-btn {
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    @media (max-width: 900px) {
      height: auto;
      padding: 10px 12px 12px;
    }
  }
}

/* Mobile Styles */
.settings-mobile-nav {
  display: none;
  overflow-x: auto;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
  padding: 0;
  padding-left: 42px;
  flex-shrink: 0;

  @media (max-width: 900px) {
    display: flex;
  }

  .nav-item {
    padding: 12px 16px;
    white-space: nowrap;
    cursor: pointer;
    color: var(--gray-600);
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: all 0.2s;

    &.active {
      color: var(--main-color);
      border-bottom-color: var(--main-color);
    }
  }
}
</style>
