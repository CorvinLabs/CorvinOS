export const formatTime = (timestamp?: string) => {
  if (!timestamp) return "—";
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return "—";
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const mins = Math.floor(diff / 60000);
  const hours = Math.floor(mins / 60);
  const days = Math.floor(hours / 24);

  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString();
};

export const formatHealth = (score: number) => `${(score * 100).toFixed(0)}%`;

export const getStatusColor = (status: string): string => {
  const colors: Record<string, string> = {
    active: "bg-green-100 text-green-800 border-green-300",
    dormant: "bg-yellow-100 text-yellow-800 border-yellow-300",
    stale: "bg-orange-100 text-orange-800 border-orange-300",
    degrading: "bg-red-100 text-red-800 border-red-300",
  };
  return colors[status] || "bg-gray-100 text-gray-800 border-gray-300";
};
