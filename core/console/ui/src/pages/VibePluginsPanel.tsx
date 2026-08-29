/**
 * Phase 4: Vibe Plugins Management Panel (ADR-0249 Stage 6)
 *
 * Displays installed plugins, allows install/disable/uninstall.
 * Complete installation flow: Upload → Verify → Install → Enable
 * Console-integrated UI for /v1/console/plugins/* API.
 */

import React, { useState, useEffect } from "react";
import {
  Card,
  Button,
  Input,
  Tabs,
  Table,
  message,
  Upload,
  Modal,
  Space,
  Tag,
  Tooltip,
  Empty,
  Drawer,
  Spin,
  Progress,
  Alert,
  Divider,
  Typography,
  Row,
  Col,
} from "antd";
import {
  UploadOutlined,
  DeleteOutlined,
  StopOutlined,
  ReloadOutlined,
  PlusOutlined,
  InfoOutlined,
  FileZipOutlined,
  SafetyOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  CloudUploadOutlined,
} from "@ant-design/icons";
import PluginTrustBadge from "../components/PluginTrustBadge";

const { Dragger } = Upload;
const { Text, Paragraph } = Typography;

interface Plugin {
  id: string;
  version: string;
  author: string;
  description: string;
  skills?: any[];
  origin?: "builtin" | "vetted" | "community";
  pii_risk?: "none" | "low" | "medium" | "high";
  locality?: "local" | "eu_cloud" | "us_cloud" | "unknown";
  network_egress?: "none" | "local" | "external";
  egress_hosts?: string[];
  requires_consent?: boolean;
}

interface PluginListResponse {
  loaded: Plugin[];
  failed: Record<string, string>;
  count_total: number;
}

interface PluginUploadResponse {
  plugin_id: string;
  version: string;
  status: "installed" | "installed_pending_enable" | "error";
  message: string;
  trust_verdict?: "vetted" | "community" | "forged";
  requires_consent?: boolean;
  health_check_passed?: boolean;
}

interface InstallationStage {
  stage: "upload" | "verify" | "audit" | "install" | "enable" | "health_check";
  status: "pending" | "running" | "success" | "error";
  message: string;
}

interface UploadState {
  visible: boolean;
  uploading: boolean;
  stages: InstallationStage[];
  selectedFile: File | null;
  checksum: string;
  autoEnable: boolean;
}

interface MarketplacePlugin {
  plugin_id: string;
  name: string;
  version: string;
  category: string;
  origin: "builtin" | "vetted" | "community";
  author: string;
  author_email: string;
  description: string;
  long_description?: string;
  rating: number;
  rating_count: number;
  download_count: number;
  pii_risk: "none" | "low" | "medium" | "high";
  locality: "local" | "eu_cloud" | "us_cloud";
  network_egress: "none" | "local" | "external";
  egress_hosts: string[];
  trust_badge?: "verified" | "community";
  requires_consent: boolean;
  homepage_url?: string;
  repository_url?: string;
  dependencies: string[];
  listed: boolean;
}

interface MarketplaceResponse {
  plugins: MarketplacePlugin[];
  total: number;
  limit: number;
  offset: number;
}

interface MarketplaceState {
  plugins: MarketplacePlugin[];
  loading: boolean;
  error: string | null;
  searchQuery: string;
  categoryFilter: string;
  sortBy: "rating" | "downloads" | "recent";
  offset: number;
  limit: number;
}

const MARKETPLACE_CATEGORIES = [
  "All Categories",
  "Authentication",
  "Analytics",
  "Database",
  "Security",
  "Tooling",
  "Integration",
  "Performance",
  "UI",
];

const VibePluginsPanel: React.FC = () => {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [failed, setFailed] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [governanceDrawerVisible, setGovernanceDrawerVisible] = useState(false);
  const [selectedPlugin, setSelectedPlugin] = useState<Plugin | null>(null);
  const [reportingPlugin, setReportingPlugin] = useState<string | null>(null);

  // Marketplace state
  const [marketplace, setMarketplace] = useState<MarketplaceState>({
    plugins: [],
    loading: false,
    error: null,
    searchQuery: "",
    categoryFilter: "All Categories",
    sortBy: "rating",
    offset: 0,
    limit: 20,
  });

  // File upload state
  const [uploadState, setUploadState] = useState<UploadState>({
    visible: false,
    uploading: false,
    stages: [],
    selectedFile: null,
    checksum: "",
    autoEnable: false,
  });

  // Installation result state
  const [installResult, setInstallResult] = useState<PluginUploadResponse | null>(null);
  const [showResult, setShowResult] = useState(false);

  useEffect(() => {
    loadPlugins();
    loadMarketplace();
  }, []);

  const loadPlugins = async () => {
    setLoading(true);
    try {
      const res = await fetch("/v1/console/plugins");
      const data: any = await res.json();
      setPlugins(data.plugins || []);
      setFailed({});
    } catch (err) {
      message.error("Failed to load plugins");
    } finally {
      setLoading(false);
    }
  };

  const loadMarketplace = async (
    query?: string,
    category?: string,
    sort?: string,
    offset?: number
  ) => {
    setMarketplace((prev) => ({ ...prev, loading: true, error: null }));

    try {
      const params = new URLSearchParams();
      if (query) params.append("query", query);
      if (category && category !== "All Categories") {
        params.append("category", category);
      }
      if (sort) params.append("sort", sort);
      if (offset !== undefined) params.append("offset", String(offset));
      params.append("limit", String(marketplace.limit));

      const url = `/v1/vibe/plugins/marketplace?${params.toString()}`;
      const res = await fetch(url);

      if (!res.ok) {
        throw new Error("Failed to load marketplace");
      }

      const data: MarketplaceResponse = await res.json();
      setMarketplace((prev) => ({
        ...prev,
        plugins: data.plugins || [],
        loading: false,
      }));
    } catch (err) {
      setMarketplace((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : "Unknown error",
      }));
      message.error("Failed to load marketplace plugins");
    }
  };

  const computeFileChecksum = async (file: File): Promise<string> => {
    /**Compute SHA256 checksum of file for integrity verification.*/
    const buffer = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
  };

  const addStage = (stage: InstallationStage) => {
    setUploadState((prev) => ({
      ...prev,
      stages: [...prev.stages, stage],
    }));
  };

  const updateStage = (stageName: string, status: string, message: string) => {
    setUploadState((prev) => ({
      ...prev,
      stages: prev.stages.map((s) =>
        s.stage === stageName ? { ...s, status: status as any, message } : s
      ),
    }));
  };

  const handleFileSelect = async (file: File) => {
    /**Handle file selection and compute checksum.*/
    if (!file.name.endsWith(".tar.gz") && !file.name.endsWith(".tgz")) {
      message.error("Plugin must be a .tar.gz archive");
      return false;
    }

    setUploadState((prev) => ({
      ...prev,
      selectedFile: file,
    }));

    // Compute checksum
    try {
      const checksum = await computeFileChecksum(file);
      setUploadState((prev) => ({
        ...prev,
        checksum,
      }));
      message.success(`File selected (SHA256: ${checksum.slice(0, 16)}...)`);
    } catch (err) {
      message.error("Failed to compute checksum");
      return false;
    }

    return false; // Prevent default upload behavior
  };

  const handleInstallPlugin = async () => {
    /**Execute complete installation flow.*/
    if (!uploadState.selectedFile) {
      message.error("Please select a plugin file");
      return;
    }

    // Reset stages
    setUploadState((prev) => ({
      ...prev,
      stages: [],
      uploading: true,
    }));

    const stages: InstallationStage[] = [
      { stage: "upload", status: "running", message: "Uploading plugin archive..." },
      { stage: "verify", status: "pending", message: "Verifying manifest and schema..." },
      { stage: "audit", status: "pending", message: "Emitting audit event..." },
      { stage: "install", status: "pending", message: "Installing plugin..." },
      { stage: "enable", status: "pending", message: "Enabling plugin..." },
      { stage: "health_check", status: "pending", message: "Running health check..." },
    ];

    setUploadState((prev) => ({
      ...prev,
      stages,
    }));

    try {
      // Stage 1: Upload
      const formData = new FormData();
      formData.append("file", uploadState.selectedFile);
      if (uploadState.checksum) {
        formData.append("checksum", uploadState.checksum);
      }
      if (uploadState.autoEnable) {
        formData.append("auto_enable", "true");
      }

      updateStage("upload", "running", "Uploading plugin archive...");

      const res = await fetch("/v1/console/plugins/upload", {
        method: "POST",
        body: formData,
      });

      updateStage("upload", "success", "Upload complete");
      updateStage("verify", "running", "Verifying manifest and schema...");
      updateStage("audit", "running", "Emitting audit event...");
      updateStage("install", "running", "Installing plugin...");

      const data: PluginUploadResponse = await res.json();

      if (!res.ok) {
        updateStage("upload", "error", data.message || "Upload failed");
        setInstallResult(data);
        setShowResult(true);
        message.error(data.message || "Installation failed");
        return;
      }

      // Mark all stages as success
      updateStage("verify", "success", "Manifest verified");
      updateStage("audit", "success", "Audit event emitted");
      updateStage("install", "success", "Plugin installed");

      if (uploadState.autoEnable) {
        updateStage("enable", "success", "Plugin enabled");
      } else {
        updateStage("enable", "success", "Plugin ready (enable manually)");
      }

      if (data.health_check_passed) {
        updateStage("health_check", "success", "Health check passed");
      } else {
        updateStage("health_check", "success", "Health check skipped");
      }

      setInstallResult(data);
      setShowResult(true);

      message.success(`Plugin ${data.plugin_id}@${data.version} installed successfully`);

      // Reload plugin list after a short delay
      setTimeout(() => {
        loadPlugins();
        handleCloseUploadModal();
      }, 2000);
    } catch (err) {
      updateStage("upload", "error", `Error: ${err}`);
      message.error("Installation request failed");
    } finally {
      setUploadState((prev) => ({
        ...prev,
        uploading: false,
      }));
    }
  };

  const handleCloseUploadModal = () => {
    setUploadState({
      visible: false,
      uploading: false,
      stages: [],
      selectedFile: null,
      checksum: "",
      autoEnable: false,
    });
    setShowResult(false);
    setInstallResult(null);
  };

  const handleDisable = async (pluginId: string) => {
    try {
      const res = await fetch(`/v1/console/plugins/${pluginId}/disable`, {
        method: "POST",
      });
      const data = await res.json();

      if (res.ok) {
        message.success(`Plugin ${pluginId} disabled`);
        loadPlugins();
      } else {
        message.error(data.detail || "Disable failed");
      }
    } catch (err) {
      message.error("Disable request failed");
    }
  };

  const handleUninstall = async (pluginId: string) => {
    Modal.confirm({
      title: "Uninstall Plugin",
      content: `Are you sure you want to uninstall ${pluginId}?`,
      okText: "Yes",
      cancelText: "No",
      onOk: async () => {
        try {
          const res = await fetch(
            `/v1/console/plugins/${pluginId}`,
            { method: "DELETE" }
          );
          const data = await res.json();

          if (res.ok) {
            message.success(`Plugin ${pluginId} uninstalled`);
            loadPlugins();
          } else {
            message.error(data.detail || "Uninstall failed");
          }
        } catch (err) {
          message.error("Uninstall request failed");
        }
      },
    });
  };

  const handleShowGovernance = (plugin: Plugin) => {
    setSelectedPlugin(plugin);
    setGovernanceDrawerVisible(true);
  };

  const handleReportPlugin = async (
    plugin_id: string,
    reason: string,
    details: string
  ) => {
    setReportingPlugin(plugin_id);
    try {
      const res = await fetch(`/v1/console/plugins/${plugin_id}/report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason, details }),
      });

      const data = await res.json();

      if (res.ok && data.status === "success") {
        message.success("Report submitted successfully");
        return;
      } else {
        throw new Error(data.detail || data.error || "Failed to submit report");
      }
    } catch (err) {
      message.error(`Report submission failed: ${err}`);
      throw err;
    } finally {
      setReportingPlugin(null);
    }
  };

  const handleMarketplaceSearch = (value: string) => {
    setMarketplace((prev) => ({
      ...prev,
      searchQuery: value,
      offset: 0,
    }));
    loadMarketplace(value, marketplace.categoryFilter, marketplace.sortBy, 0);
  };

  const handleCategoryChange = (value: string) => {
    setMarketplace((prev) => ({
      ...prev,
      categoryFilter: value,
      offset: 0,
    }));
    loadMarketplace(
      marketplace.searchQuery,
      value,
      marketplace.sortBy,
      0
    );
  };

  const handleSortChange = (value: "rating" | "downloads" | "recent") => {
    setMarketplace((prev) => ({
      ...prev,
      sortBy: value,
      offset: 0,
    }));
    loadMarketplace(
      marketplace.searchQuery,
      marketplace.categoryFilter,
      value,
      0
    );
  };

  const handleInstallFromMarketplace = (plugin: MarketplacePlugin) => {
    // For now, open the upload modal with plugin metadata
    // In k=2, this can be enhanced to auto-trigger installation
    message.info(
      `Install ${plugin.name}@${plugin.version} by uploading the plugin file`
    );
    setUploadState((prev) => ({
      ...prev,
      visible: true,
    }));
  };

  const pluginColumns = [
    {
      title: "Plugin ID",
      dataIndex: "id",
      key: "id",
      render: (id: string) => <strong>{id}</strong>,
    },
    {
      title: "Author",
      dataIndex: "author",
      key: "author",
    },
    {
      title: "Version",
      dataIndex: "version",
      key: "version",
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: "Trust",
      dataIndex: "origin",
      key: "origin",
      render: (origin: string | undefined) => {
        if (!origin) return <Tag>Unknown</Tag>;
        const colors: Record<string, string> = {
          builtin: "blue",
          vetted: "green",
          community: "orange",
        };
        return <Tag color={colors[origin] || "default"}>{origin}</Tag>;
      },
    },
    {
      title: "Skills",
      dataIndex: "skills",
      key: "skills",
      render: (skills: any[] | undefined) => skills?.length || 0,
    },
    {
      title: "Actions",
      key: "actions",
      render: (_, record: Plugin) => (
        <Space size="small">
          <Tooltip title="View governance & trust">
            <Button
              size="small"
              icon={<InfoOutlined />}
              onClick={() => handleShowGovernance(record)}
            />
          </Tooltip>
          <Tooltip title="Disable plugin">
            <Button
              size="small"
              icon={<StopOutlined />}
              onClick={() => handleDisable(record.id)}
              danger
            />
          </Tooltip>
          <Tooltip title="Uninstall plugin">
            <Button
              size="small"
              icon={<DeleteOutlined />}
              onClick={() => handleUninstall(record.id)}
              danger
              type="primary"
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: "24px" }}>
      <Card
        title="Vibe Engineering Plugins"
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={loadPlugins}
              loading={loading}
            >
              Refresh
            </Button>
            <Button
              type="primary"
              icon={<UploadOutlined />}
              onClick={() =>
                setUploadState((prev) => ({
                  ...prev,
                  visible: true,
                }))
              }
            >
              Install from File
            </Button>
          </Space>
        }
      >
        <Tabs
          items={[
            {
              key: "marketplace",
              label: `Marketplace (${marketplace.plugins.length})`,
              children: (
                <div style={{ padding: "16px 0" }}>
                  {/* Search and Filters */}
                  <Row gutter={16} style={{ marginBottom: "16px" }}>
                    <Col xs={24} sm={12} md={8}>
                      <Input.Search
                        placeholder="Search plugins..."
                        value={marketplace.searchQuery}
                        onChange={(e) =>
                          handleMarketplaceSearch(e.target.value)
                        }
                        allowClear
                        style={{ width: "100%" }}
                      />
                    </Col>
                    <Col xs={24} sm={12} md={8}>
                      <Select
                        style={{ width: "100%" }}
                        value={marketplace.categoryFilter}
                        onChange={handleCategoryChange}
                        options={MARKETPLACE_CATEGORIES.map((cat) => ({
                          label: cat,
                          value: cat,
                        }))}
                      />
                    </Col>
                    <Col xs={24} sm={12} md={8}>
                      <Select
                        style={{ width: "100%" }}
                        value={marketplace.sortBy}
                        onChange={handleSortChange}
                        options={[
                          { label: "Rating", value: "rating" },
                          { label: "Downloads", value: "downloads" },
                          { label: "Recent", value: "recent" },
                        ]}
                      />
                    </Col>
                  </Row>

                  {marketplace.error && (
                    <Alert
                      type="error"
                      message={marketplace.error}
                      showIcon
                      style={{ marginBottom: "16px" }}
                    />
                  )}

                  {marketplace.loading ? (
                    <div style={{ textAlign: "center", padding: "40px" }}>
                      <Spin />
                    </div>
                  ) : marketplace.plugins.length > 0 ? (
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns:
                          "repeat(auto-fill, minmax(300px, 1fr))",
                        gap: "16px",
                      }}
                    >
                      {marketplace.plugins.map((plugin) => (
                        <Card
                          key={plugin.plugin_id}
                          hoverable
                          style={{ height: "100%" }}
                        >
                          <Space
                            direction="vertical"
                            size="small"
                            style={{ width: "100%" }}
                          >
                            <div>
                              <strong>{plugin.name}</strong>
                              <br />
                              <Text type="secondary" style={{ fontSize: "12px" }}>
                                v{plugin.version}
                              </Text>
                            </div>

                            <PluginTrustBadge
                              plugin_id={plugin.plugin_id}
                              governance={{
                                origin: plugin.origin,
                                author: plugin.author,
                                pii_risk: plugin.pii_risk,
                                locality: plugin.locality,
                                network_egress: plugin.network_egress,
                                egress_hosts: plugin.egress_hosts,
                                requires_consent: plugin.requires_consent,
                              }}
                            />

                            <Text
                              type="secondary"
                              style={{ fontSize: "14px", minHeight: "40px" }}
                            >
                              {plugin.description}
                            </Text>

                            <Row gutter={8}>
                              <Col span={12}>
                                <Text type="secondary" style={{ fontSize: "12px" }}>
                                  ⭐ {plugin.rating.toFixed(1)} ({plugin.rating_count})
                                </Text>
                              </Col>
                              <Col span={12}>
                                <Text type="secondary" style={{ fontSize: "12px" }}>
                                  ⬇️ {(plugin.download_count / 1000).toFixed(1)}K
                                </Text>
                              </Col>
                            </Row>

                            <Button
                              type="primary"
                              block
                              onClick={() =>
                                handleInstallFromMarketplace(plugin)
                              }
                              icon={<CloudUploadOutlined />}
                            >
                              Install
                            </Button>
                          </Space>
                        </Card>
                      ))}
                    </div>
                  ) : (
                    <Empty description="No plugins found" />
                  )}
                </div>
              ),
            },
            {
              key: "installed",
              label: `Installed (${plugins.length})`,
              children:
                plugins.length > 0 ? (
                  <Table
                    dataSource={plugins}
                    columns={pluginColumns}
                    rowKey="id"
                    pagination={{ pageSize: 10 }}
                  />
                ) : (
                  <Empty description="No plugins installed" />
                ),
            },
            {
              key: "failed",
              label: `Failed (${Object.keys(failed).length})`,
              children:
                Object.keys(failed).length > 0 ? (
                  <Table
                    dataSource={Object.entries(failed).map(([id, reason]) => ({
                      id,
                      reason,
                    }))}
                    columns={[
                      { title: "Plugin ID", dataIndex: "id", key: "id" },
                      {
                        title: "Error",
                        dataIndex: "reason",
                        key: "reason",
                        render: (r: string) => (
                          <span style={{ color: "red" }}>{r}</span>
                        ),
                      },
                    ]}
                    rowKey="id"
                    pagination={{ pageSize: 10 }}
                  />
                ) : (
                  <Empty description="No failed plugins" />
                ),
            },
          ]}
        />
      </Card>

      {/* Upload Modal — ADR-0249 Stage 6 Installation Flow */}
      <Modal
        title="Install Plugin"
        open={uploadState.visible}
        onOk={
          showResult ? () => handleCloseUploadModal() : () => handleInstallPlugin()
        }
        onCancel={handleCloseUploadModal}
        confirmLoading={uploadState.uploading}
        width={700}
        okText={showResult ? "Close" : "Install"}
      >
        {!showResult ? (
          <div>
            {/* File Upload Section */}
            <div style={{ marginBottom: "24px" }}>
              <Text strong>Step 1: Upload Plugin Archive</Text>
              <Divider />
              <Dragger
                maxCount={1}
                accept=".tar.gz,.tgz"
                beforeUpload={handleFileSelect}
                onRemove={() =>
                  setUploadState((prev) => ({
                    ...prev,
                    selectedFile: null,
                    checksum: "",
                  }))
                }
              >
                {uploadState.selectedFile ? (
                  <div style={{ padding: "16px" }}>
                    <CheckCircleOutlined
                      style={{ fontSize: "32px", color: "#52c41a" }}
                    />
                    <Paragraph style={{ marginTop: "8px" }}>
                      <Text strong>{uploadState.selectedFile.name}</Text>
                      <br />
                      <Text type="secondary">
                        {(uploadState.selectedFile.size / 1024 / 1024).toFixed(2)} MB
                      </Text>
                      <br />
                      {uploadState.checksum && (
                        <Text type="secondary" code style={{ fontSize: "11px" }}>
                          SHA256: {uploadState.checksum.slice(0, 24)}...
                        </Text>
                      )}
                    </Paragraph>
                  </div>
                ) : (
                  <div style={{ padding: "24px" }}>
                    <CloudUploadOutlined
                      style={{ fontSize: "48px", color: "#1890ff", marginBottom: "8px" }}
                    />
                    <Paragraph>
                      <Text strong>Drag and drop your plugin archive</Text>
                      <br />
                      or click to browse (.tar.gz)
                    </Paragraph>
                  </div>
                )}
              </Dragger>
            </div>

            {/* Options Section */}
            <div style={{ marginBottom: "24px" }}>
              <Text strong>Step 2: Installation Options</Text>
              <Divider />
              <div style={{ marginBottom: "12px" }}>
                <label style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <input
                    type="checkbox"
                    checked={uploadState.autoEnable}
                    onChange={(e) =>
                      setUploadState((prev) => ({
                        ...prev,
                        autoEnable: e.target.checked,
                      }))
                    }
                  />
                  <span>Automatically enable plugin after installation</span>
                </label>
              </div>
            </div>

            {/* Info Alert */}
            <Alert
              message="Installation Flow"
              description="The plugin will be verified for schema, integrity, and trust level. If trust enforcement is enabled, community plugins will require consent."
              type="info"
              showIcon
              icon={<InfoOutlined />}
            />
          </div>
        ) : (
          <div>
            {/* Installation Result */}
            <div style={{ marginBottom: "24px" }}>
              {installResult?.status === "error" ? (
                <Alert
                  message="Installation Failed"
                  description={installResult.message}
                  type="error"
                  showIcon
                  icon={<ExclamationCircleOutlined />}
                />
              ) : (
                <Alert
                  message="Installation Successful"
                  description={`${installResult?.plugin_id}@${installResult?.version} installed`}
                  type="success"
                  showIcon
                  icon={<CheckCircleOutlined />}
                />
              )}
            </div>

            {/* Trust Information */}
            {installResult?.trust_verdict && (
              <div style={{ marginBottom: "24px" }}>
                <Text strong>Trust Verdict</Text>
                <Divider />
                <Tag
                  color={
                    installResult.trust_verdict === "vetted"
                      ? "green"
                      : installResult.trust_verdict === "community"
                      ? "orange"
                      : "red"
                  }
                >
                  {installResult.trust_verdict.toUpperCase()}
                </Tag>
                {installResult.requires_consent && (
                  <Text
                    type="warning"
                    style={{ display: "block", marginTop: "8px" }}
                  >
                    <SafetyOutlined /> Community plugin requires consent
                  </Text>
                )}
              </div>
            )}

            {/* Installation Stages */}
            <div>
              <Text strong>Installation Stages</Text>
              <Divider />
              {uploadState.stages.map((stage) => (
                <div key={stage.stage} style={{ marginBottom: "12px" }}>
                  <Row justify="space-between" align="middle">
                    <Col>
                      <Text>
                        {stage.stage.charAt(0).toUpperCase() + stage.stage.slice(1)}
                      </Text>
                    </Col>
                    <Col>
                      {stage.status === "success" && (
                        <CheckCircleOutlined style={{ color: "#52c41a" }} />
                      )}
                      {stage.status === "error" && (
                        <ExclamationCircleOutlined style={{ color: "#f5222d" }} />
                      )}
                      {stage.status === "running" && (
                        <Spin size="small" />
                      )}
                    </Col>
                  </Row>
                  <Text type="secondary" style={{ fontSize: "12px" }}>
                    {stage.message}
                  </Text>
                </div>
              ))}
            </div>
          </div>
        )}
      </Modal>

      {/* Governance & Trust Drawer */}
      <Drawer
        title={
          selectedPlugin ? `Plugin: ${selectedPlugin.id}` : "Plugin Governance"
        }
        placement="right"
        onClose={() => {
          setGovernanceDrawerVisible(false);
          setSelectedPlugin(null);
        }}
        open={governanceDrawerVisible}
        width={600}
      >
        {selectedPlugin ? (
          <Spin spinning={reportingPlugin === selectedPlugin.id}>
            <PluginTrustBadge
              plugin_id={selectedPlugin.id}
              governance={{
                origin: selectedPlugin.origin || "community",
                author: selectedPlugin.author,
                pii_risk: selectedPlugin.pii_risk || "low",
                locality: selectedPlugin.locality || "unknown",
                network_egress: selectedPlugin.network_egress || "external",
                egress_hosts: selectedPlugin.egress_hosts,
                requires_consent: selectedPlugin.requires_consent || false,
              }}
              onReport={handleReportPlugin}
            />
          </Spin>
        ) : (
          <p>Select a plugin to view governance information</p>
        )}
      </Drawer>
    </div>
  );
};

export default VibePluginsPanel;
