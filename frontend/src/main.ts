import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import '@/styles/main.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)

// 全局兜底（G6.3）：捕获未被 ErrorBoundary 就地接管的异常（如事件处理器内的
// 未捕获错误），至少落日志，避免静默丢失。
app.config.errorHandler = (err, _instance, info) => {
  console.error(`[app] ${info || 'unhandled'}`, err)
}

app.mount('#app')
