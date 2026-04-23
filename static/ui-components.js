(() => {
const AppCard = {
  template: `<section class="card"><slot></slot></section>`,
};

const ToastBanner = {
  props: ["notice"],
  template: `<div v-if="notice && notice.message" class="mb-6 rounded-lg border px-4 py-3 text-sm"
    :class="notice.type === 'error' ? 'border-red-200 bg-red-50 text-red-700' : 'border-slate-200 bg-slate-50 text-slate-700'">
    {{ notice.message }}
  </div>`,
};

const EmptyState = {
  props: ["text"],
  template: `<div class="rounded border border-dashed p-4 text-center text-gray-400">{{ text || '暂无数据' }}</div>`,
};

const MappingOptionBadges = {
  props: ["item"],
  computed: {
    badges() {
      const item = this.item || {};
      const values = [item.realtime_sender === "user" ? "辅助账号" : "机器人"];
      if (item.realtime_sender === "bot" && item.realtime_fallback_to_user) values.push("Bot失败回退");
      if (item.realtime_hash_perturb) values.push("重置指纹");
      return values;
    },
  },
  template: `<div class="flex flex-wrap gap-1.5">
    <span v-for="badge in badges" :key="badge" class="mapping-badge">{{ badge }}</span>
  </div>`,
};

const LogPanel = {
  components: { AppCard },
  props: ["title", "description", "logs", "kind", "panelId"],
  emits: ["clear"],
  methods: {
    levelClass(value) {
      const text = String(value || "");
      if (text === "ERROR" || text.includes("ERROR") || text.includes("DROP")) return "text-red-300 bg-red-950/60 border-red-900/70";
      if (text === "WARNING" || text.includes("WARN") || text.includes("FALLBACK")) return "text-amber-300 bg-amber-950/50 border-amber-900/70";
      if (text === "SUCCESS" || text.includes("SEND") || text.includes("MAP")) return "text-emerald-300 bg-emerald-950/50 border-emerald-900/70";
      if (text.includes("SKIP")) return "text-yellow-200 bg-yellow-950/40 border-yellow-900/60";
      if (text.includes("REWRITE")) return "text-cyan-300 bg-cyan-950/40 border-cyan-900/60";
      return "text-sky-200 bg-slate-800/70 border-slate-700";
    },
    label(log) {
      return this.kind === "message" ? log.action : log.level;
    },
    body(log) {
      return this.kind === "message" ? log.detail : log.msg;
    },
  },
  template: `<app-card>
    <div class="mb-3">
      <h2 class="text-lg font-semibold">{{ title }}</h2>
      <p class="text-xs text-gray-500">{{ description }}</p>
      <div class="mt-3 flex justify-end">
        <button @click="$emit('clear')" class="btn-secondary shrink-0 !px-3 !py-1 text-xs">清理</button>
      </div>
    </div>
    <div :id="panelId" class="log-panel">
      <div v-for="log in logs" :key="log.id" class="border-b border-slate-800 pb-2 text-slate-200">
        <div class="grid grid-cols-[150px_minmax(0,1fr)] items-start gap-3">
          <div class="shrink-0 space-y-1">
            <div class="text-[11px] leading-tight text-slate-500">[{{ log.time }}]</div>
            <div :class="levelClass(label(log))" class="inline-block rounded border px-1.5 py-0.5 text-[10px] leading-none font-semibold">[{{ label(log) }}]</div>
          </div>
          <div class="min-w-0 break-all text-slate-200">{{ body(log) }}</div>
        </div>
      </div>
      <div v-if="!(logs || []).length" class="text-slate-500">暂无{{ title }}</div>
    </div>
  </app-card>`,
};

window.TgcsUi = {
  AppCard,
  ToastBanner,
  EmptyState,
  MappingOptionBadges,
  LogPanel,
};
})();
