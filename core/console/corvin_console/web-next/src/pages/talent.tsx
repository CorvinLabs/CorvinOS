/**
 * Your Talent — Enhanced Dashboard with Charts
 *
 * Displays Self-Learning Talent Dashboard (CONCEPT-0003)
 * with interactive visualizations of system performance
 */

"use client";

import { useEffect, useState } from "react";
import { Loader2, AlertCircle, TrendingUp, BarChart3, Zap } from "lucide-react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ScatterChart,
  Scatter,
} from "recharts";

interface TalentData {
  timestamp: string;
  talent_score: number;
  trend: number;
  components: {
    accuracy: number;
    learning_rate: number;
    variety: number;
    efficiency: number;
  };
  ranking: Array<{
    id: string;
    rank: number;
    medal: string;
    status: string;
    accuracy: number;
    feedback_pct: number;
  }>;
  events: Array<{
    timestamp: string;
    type: string;
    title: string;
    description: string;
    badge: string;
  }>;
}

interface DailyData {
  date: string;
  score: number;
  accuracy: number;
  learning_rate: number;
  variety: number;
  efficiency: number;
  record_count: number;
}

interface TaskTypeData {
  type: string;
  count: number;
  accuracy: number;
  feedback_percentage: number;
  efficiency: number;
}

export default function YourTalentPage() {
  const [talentData, setTalentData] = useState<TalentData | null>(null);
  const [dailyData, setDailyData] = useState<DailyData[]>([]);
  const [taskTypes, setTaskTypes] = useState<TaskTypeData[]>([]);
  const [correlation, setCorrelation] = useState<any>(null);
  const [insights, setInsights] = useState<any>(null);
  const [story, setStory] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<"overview" | "insights" | "history" | "analysis">("overview");

  const API_BASE = "http://127.0.0.1:5000";

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);

        // Fetch all data in parallel
        const [scoreRes, historyRes, taskRes, corrRes, insightsRes, storyRes] = await Promise.all([
          fetch(`${API_BASE}/api/v1/talent/score`),
          fetch(`${API_BASE}/api/v1/talent/history?days=7`),
          fetch(`${API_BASE}/api/v1/talent/task-types?days=7`),
          fetch(`${API_BASE}/api/v1/talent/correlation?days=7`),
          fetch(`${API_BASE}/api/v1/talent/insights?days=7`),
          fetch(`${API_BASE}/api/v1/talent/story?days=7`),
        ]);

        if (!scoreRes.ok) throw new Error("Failed to fetch talent score");

        const score = await scoreRes.json();
        setTalentData(score);

        if (historyRes.ok) {
          const history = await historyRes.json();
          setDailyData(history.daily || []);
        }

        if (taskRes.ok) {
          const tasks = await taskRes.json();
          setTaskTypes(tasks.task_types || []);
        }

        if (corrRes.ok) {
          const corr = await corrRes.json();
          setCorrelation(corr.correlation || { points: [] });
        }

        if (insightsRes.ok) {
          const insightsData = await insightsRes.json();
          setInsights(insightsData);
        }

        if (storyRes.ok) {
          const storyData = await storyRes.json();
          setStory(storyData.story);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  if (loading && !talentData) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
          <p className="text-muted-foreground">Loading Your Talent Dashboard...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="flex flex-col items-center gap-4 text-red-600">
          <AlertCircle className="h-8 w-8" />
          <p className="text-muted-foreground">Error: {error}</p>
        </div>
      </div>
    );
  }

  if (!talentData) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <p className="text-muted-foreground">No data available</p>
      </div>
    );
  }

  const getTrendColor = (trend: number) => {
    if (trend > 0) return "text-green-600";
    if (trend < 0) return "text-red-600";
    return "text-gray-600";
  };

  const getScoreColor = (score: number) => {
    if (score >= 8) return "from-green-600 to-green-400";
    if (score >= 6) return "from-blue-600 to-blue-400";
    return "from-orange-600 to-orange-400";
  };

  // Prepare radar chart data
  const radarData = [
    { name: "Accuracy", value: talentData.components.accuracy * 10 },
    { name: "Learning", value: talentData.components.learning_rate * 10 },
    { name: "Variety", value: talentData.components.variety * 10 },
    { name: "Efficiency", value: talentData.components.efficiency * 10 },
  ];

  return (
    <div className="space-y-6 p-6 bg-gradient-to-br from-slate-50 to-slate-100 dark:from-slate-950 dark:to-slate-900 min-h-screen">
      {/* Header */}
      <div className="flex flex-col gap-2">
        <h1 className="text-4xl font-bold text-gray-900 dark:text-white">Your Talent</h1>
        <p className="text-muted-foreground">
          Real-time learning metrics and performance insights
        </p>
      </div>

      {/* Talent Score Card */}
      <div className={`bg-gradient-to-br ${getScoreColor(talentData.talent_score)} rounded-lg p-8 text-white shadow-lg`}>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          <div>
            <p className="text-sm font-medium opacity-90">Talent Score</p>
            <p className="text-5xl font-bold">{talentData.talent_score.toFixed(1)}</p>
            <p className="text-sm mt-2 opacity-80">/10</p>
          </div>
          <div>
            <p className="text-sm font-medium opacity-90">Trend (7d)</p>
            <p className={`text-3xl font-bold ${getTrendColor(talentData.trend)}`}>
              {talentData.trend > 0 ? "+" : ""}{talentData.trend.toFixed(1)}
            </p>
            <p className="text-sm mt-2 opacity-80">
              {talentData.trend > 0 ? "↗ Improving" : "↘ Declining"}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4 md:col-span-1">
            <div className="bg-white/20 rounded p-3">
              <p className="text-xs font-medium opacity-75">Accuracy</p>
              <p className="text-lg font-bold">{(talentData.components.accuracy * 100).toFixed(0)}%</p>
            </div>
            <div className="bg-white/20 rounded p-3">
              <p className="text-xs font-medium opacity-75">Efficiency</p>
              <p className="text-lg font-bold">{(talentData.components.efficiency * 100).toFixed(0)}%</p>
            </div>
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex gap-2 border-b border-gray-200 dark:border-gray-700 overflow-x-auto">
        <button
          onClick={() => setSelectedTab("overview")}
          className={`px-4 py-2 font-medium border-b-2 transition-colors whitespace-nowrap ${
            selectedTab === "overview"
              ? "border-blue-500 text-blue-600 dark:text-blue-400"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Overview
        </button>
        <button
          onClick={() => setSelectedTab("insights")}
          className={`px-4 py-2 font-medium border-b-2 transition-colors whitespace-nowrap ${
            selectedTab === "insights"
              ? "border-blue-500 text-blue-600 dark:text-blue-400"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          What You Learned
        </button>
        <button
          onClick={() => setSelectedTab("history")}
          className={`px-4 py-2 font-medium border-b-2 transition-colors whitespace-nowrap ${
            selectedTab === "history"
              ? "border-blue-500 text-blue-600 dark:text-blue-400"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          History & Trends
        </button>
        <button
          onClick={() => setSelectedTab("analysis")}
          className={`px-4 py-2 font-medium border-b-2 transition-colors whitespace-nowrap ${
            selectedTab === "analysis"
              ? "border-blue-500 text-blue-600 dark:text-blue-400"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Analysis
        </button>
      </div>

      {/* Overview Tab */}
      {selectedTab === "overview" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Radar Chart */}
          <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <BarChart3 className="h-5 w-5" />
              Component Balance
            </h2>
            <ResponsiveContainer width="100%" height={300}>
              <RadarChart data={radarData}>
                <PolarGrid stroke="#cbd5e1" />
                <PolarAngleAxis
                  dataKey="name"
                  stroke="#94a3b8"
                  style={{ fontSize: "12px" }}
                />
                <PolarRadiusAxis angle={90} domain={[0, 10]} stroke="#cbd5e1" />
                <Radar
                  name="Score"
                  dataKey="value"
                  stroke="#3b82f6"
                  fill="#3b82f6"
                  fillOpacity={0.6}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>

          {/* Top Contexts */}
          <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <Zap className="h-5 w-5" />
              Top Contexts
            </h2>
            <div className="space-y-3 max-h-80 overflow-y-auto">
              {talentData.ranking.slice(0, 5).map((ctx) => (
                <div
                  key={ctx.id}
                  className="flex items-center justify-between p-3 bg-slate-50 dark:bg-slate-700 rounded"
                >
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <span className="text-lg">{ctx.medal}</span>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{ctx.id}</p>
                      <p className="text-xs text-muted-foreground">
                        {ctx.status}
                      </p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-bold text-sm">
                      {(ctx.accuracy * 100).toFixed(0)}%
                    </p>
                    <p className="text-xs text-muted-foreground">accuracy</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Insights Tab — What You Learned */}
      {selectedTab === "insights" && (
        <div className="space-y-6">
          {/* Story/Journey */}
          {story && (
            <div className="bg-gradient-to-r from-purple-50 to-pink-50 dark:from-purple-900/20 dark:to-pink-900/20 rounded-lg p-6 border border-purple-200 dark:border-purple-800">
              <h2 className="text-2xl font-bold mb-3">Your Learning Journey</h2>
              <p className="text-lg text-foreground mb-4">{story.summary}</p>
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="bg-white dark:bg-slate-800 rounded p-3">
                  <p className="text-xs text-muted-foreground">Starting Score</p>
                  <p className="text-2xl font-bold">{story.score_start}</p>
                </div>
                <div className="bg-white dark:bg-slate-800 rounded p-3">
                  <p className="text-xs text-muted-foreground">Current Score</p>
                  <p className="text-2xl font-bold text-green-600">{story.score_end}</p>
                </div>
                <div className="bg-white dark:bg-slate-800 rounded p-3">
                  <p className="text-xs text-muted-foreground">Improvement</p>
                  <p className="text-2xl font-bold text-blue-600">
                    {story.score_change > 0 ? "+" : ""}{story.score_change}
                  </p>
                </div>
                <div className="bg-white dark:bg-slate-800 rounded p-3">
                  <p className="text-xs text-muted-foreground">Trend</p>
                  <p className="text-lg font-bold capitalize">{story.trend} 📈</p>
                </div>
              </div>
            </div>
          )}

          {/* Dimension Insights */}
          {insights?.dimensions && (
            <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                📊 What Changed in Each Dimension
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {insights.dimensions.map((dim: any, i: number) => (
                  <div key={i} className="bg-slate-50 dark:bg-slate-700 rounded p-4 border-l-4"
                    style={{
                      borderColor: dim.status === "up" ? "#10b981" : dim.status === "down" ? "#ef4444" : "#94a3b8"
                    }}>
                    <div className="flex items-start justify-between mb-2">
                      <div>
                        <p className="text-sm text-muted-foreground">{dim.dimension}</p>
                        <p className="text-2xl font-bold">{dim.icon}</p>
                      </div>
                      <div className="text-right">
                        <p className="text-2xl font-bold">{dim.current.toFixed(1)}%</p>
                        <p className={`text-sm font-medium ${
                          dim.status === "up" ? "text-green-600" :
                          dim.status === "down" ? "text-red-600" :
                          "text-gray-600"
                        }`}>
                          {dim.status === "up" ? "↗" : dim.status === "down" ? "↘" : "→"} {Math.abs(dim.change).toFixed(1)}%
                        </p>
                      </div>
                    </div>
                    <p className="text-sm">{dim.narrative}</p>
                    <p className="text-xs text-muted-foreground mt-2">{dim.analysis}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Learning Narratives */}
          {insights?.narratives && insights.narratives.length > 0 && (
            <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                💡 Learning Insights
              </h2>
              <div className="space-y-3">
                {insights.narratives.map((narrative: any, i: number) => (
                  <div key={i} className="flex gap-3 p-4 bg-slate-50 dark:bg-slate-700 rounded border-l-4 border-blue-500">
                    <div className="text-2xl flex-shrink-0">{narrative.icon}</div>
                    <div className="flex-1">
                      <p className="font-semibold">{narrative.title}</p>
                      <p className="text-sm text-muted-foreground">{narrative.description}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Achievement Badges */}
          {insights?.badges && insights.badges.length > 0 && (
            <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                🏆 Achievements Unlocked
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                {insights.badges.map((badge: any, i: number) => (
                  <div key={i} className="bg-gradient-to-br from-amber-50 to-orange-50 dark:from-amber-900/20 dark:to-orange-900/20 rounded-lg p-4 text-center border border-amber-200 dark:border-amber-800">
                    <div className="text-4xl mb-2">{badge.badge}</div>
                    <p className="font-bold text-sm">{badge.title}</p>
                    <p className="text-xs text-muted-foreground mt-1">{badge.context}</p>
                    <p className="text-xs font-medium capitalize mt-2 text-amber-700 dark:text-amber-400">
                      {badge.level}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* History Tab */}
      {selectedTab === "history" && (
        <div className="space-y-6">
          {/* Score Trend */}
          <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <TrendingUp className="h-5 w-5" />
              Score Trend (7 days)
            </h2>
            {dailyData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={dailyData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#cbd5e1" />
                  <XAxis
                    dataKey="date"
                    stroke="#94a3b8"
                    style={{ fontSize: "12px" }}
                  />
                  <YAxis domain={[0, 10]} stroke="#94a3b8" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#1e293b",
                      border: "1px solid #475569",
                    }}
                  />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="score"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    dot={{ fill: "#3b82f6", r: 4 }}
                    activeDot={{ r: 6 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-muted-foreground text-center py-8">
                No historical data available
              </p>
            )}
          </div>

          {/* Component Breakdown */}
          <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
            <h2 className="text-lg font-semibold mb-4">Component Trends</h2>
            {dailyData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={dailyData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#cbd5e1" />
                  <XAxis
                    dataKey="date"
                    stroke="#94a3b8"
                    style={{ fontSize: "12px" }}
                  />
                  <YAxis domain={[0, 1]} stroke="#94a3b8" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#1e293b",
                      border: "1px solid #475569",
                    }}
                  />
                  <Legend />
                  <Bar dataKey="accuracy" fill="#10b981" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="learning_rate" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="variety" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="efficiency" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-muted-foreground text-center py-8">
                No historical data available
              </p>
            )}
          </div>
        </div>
      )}

      {/* Analysis Tab */}
      {selectedTab === "analysis" && (
        <div className="space-y-6">
          {/* Task Type Performance */}
          {taskTypes.length > 0 && (
            <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
              <h2 className="text-lg font-semibold mb-4">Task Type Performance</h2>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={taskTypes}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#cbd5e1" />
                  <XAxis
                    dataKey="type"
                    stroke="#94a3b8"
                    style={{ fontSize: "12px" }}
                    angle={-45}
                    textAnchor="end"
                    height={100}
                  />
                  <YAxis stroke="#94a3b8" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#1e293b",
                      border: "1px solid #475569",
                    }}
                  />
                  <Legend />
                  <Bar
                    dataKey="accuracy"
                    fill="#10b981"
                    name="Accuracy"
                    radius={[4, 4, 0, 0]}
                  />
                  <Bar
                    dataKey="efficiency"
                    fill="#3b82f6"
                    name="Efficiency"
                    radius={[4, 4, 0, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Accuracy vs Efficiency Correlation */}
          {correlation?.points && correlation.points.length > 0 && (
            <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
              <h2 className="text-lg font-semibold mb-4">
                Accuracy vs Efficiency
              </h2>
              <ResponsiveContainer width="100%" height={300}>
                <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#cbd5e1" />
                  <XAxis
                    dataKey="accuracy"
                    name="Accuracy"
                    domain={[0, 1]}
                    stroke="#94a3b8"
                  />
                  <YAxis
                    dataKey="efficiency"
                    name="Efficiency"
                    domain={[0, 1]}
                    stroke="#94a3b8"
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#1e293b",
                      border: "1px solid #475569",
                    }}
                    cursor={{ strokeDasharray: "3 3" }}
                  />
                  <Scatter
                    name="Predictions"
                    data={correlation.points}
                    fill="#3b82f6"
                    fillOpacity={0.6}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Recent Events */}
          {talentData.events.length > 0 && (
            <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow">
              <h2 className="text-lg font-semibold mb-4">Recent Events</h2>
              <div className="space-y-3 max-h-96 overflow-y-auto">
                {talentData.events.slice(0, 10).map((event, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-3 p-3 bg-slate-50 dark:bg-slate-700 rounded"
                  >
                    <div className="text-xl flex-shrink-0">{event.badge.charAt(0)}</div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium">{event.title}</p>
                      <p className="text-sm text-muted-foreground">
                        {event.description}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1">
                        {new Date(event.timestamp).toLocaleString()}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
