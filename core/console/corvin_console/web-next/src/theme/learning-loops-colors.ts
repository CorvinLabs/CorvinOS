export const learningLoopsColors = {
  light: {
    status: {
      active: { bg: "bg-green-100", text: "text-green-800", border: "border-green-300" },
      dormant: { bg: "bg-yellow-100", text: "text-yellow-800", border: "border-yellow-300" },
      stale: { bg: "bg-orange-100", text: "text-orange-800", border: "border-orange-300" },
      degrading: { bg: "bg-red-100", text: "text-red-800", border: "border-red-300" },
    },
    health: {
      good: "bg-emerald-600",
      fair: "bg-amber-600",
      poor: "bg-rose-600",
    },
    trend: {
      up: "text-emerald-600",
      down: "text-rose-600",
      flat: "text-gray-600",
    },
    eventTypes: {
      outcome_feedback: "bg-blue-100 text-blue-800",
      skill_executed: "bg-purple-100 text-purple-800",
      learning_event: "bg-indigo-100 text-indigo-800",
      skill_config_updated: "bg-cyan-100 text-cyan-800",
      audit_event: "bg-slate-100 text-slate-800",
    },
  },
  dark: {
    status: {
      active: { bg: "bg-green-900", text: "text-green-100", border: "border-green-700" },
      dormant: { bg: "bg-yellow-900", text: "text-yellow-100", border: "border-yellow-700" },
      stale: { bg: "bg-orange-900", text: "text-orange-100", border: "border-orange-700" },
      degrading: { bg: "bg-red-900", text: "text-red-100", border: "border-red-700" },
    },
    health: {
      good: "bg-emerald-600",
      fair: "bg-amber-600",
      poor: "bg-rose-600",
    },
    trend: {
      up: "text-emerald-400",
      down: "text-rose-400",
      flat: "text-gray-400",
    },
    eventTypes: {
      outcome_feedback: "bg-blue-900 text-blue-100",
      skill_executed: "bg-purple-900 text-purple-100",
      learning_event: "bg-indigo-900 text-indigo-100",
      skill_config_updated: "bg-cyan-900 text-cyan-100",
      audit_event: "bg-slate-900 text-slate-100",
    },
  },
};

export const getStatusColor = (status: string, isDark: boolean = false) => {
  const colors = isDark ? learningLoopsColors.dark.status : learningLoopsColors.light.status;
  const statusColor = colors[status as keyof typeof colors];
  return statusColor ? `${statusColor.bg} ${statusColor.text} border ${statusColor.border}` : "";
};

export const getHealthColor = (score: number, isDark: boolean = false) => {
  const colors = isDark ? learningLoopsColors.dark.health : learningLoopsColors.light.health;
  if (score >= 0.7) return colors.good;
  if (score >= 0.4) return colors.fair;
  return colors.poor;
};
