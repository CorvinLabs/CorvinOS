/**
 * Phase 4: Vibe Plugins Management Panel
 *
 * Displays installed plugins, allows install/disable/uninstall.
 * Console-integrated UI for /v1/vibe/plugins API.
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
} from "antd";
import {
  UploadOutlined,
  DeleteOutlined,
  StopOutlined,
  ReloadOutlined,
  PlusOutlined,
} from "@ant-design/icons";

interface Plugin {
  id: string;
  version: string;
  author: string;
  description: string;
  skills?: any[];
}

interface PluginListResponse {
  loaded: Plugin[];
  failed: Record<string, string>;
  count_total: number;
}

const VibePluginsPanel: React.FC = () => {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [failed, setFailed] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [manifestJson, setManifestJson] = useState("");
  const [modalVisible, setModalVisible] = useState(false);

  useEffect(() => {
    loadPlugins();
  }, []);

  const loadPlugins = async () => {
    setLoading(true);
    try {
      const res = await fetch("/v1/vibe/plugins/list");
      const data: PluginListResponse = await res.json();
      setPlugins(data.loaded || []);
      setFailed(data.failed || {});
    } catch (err) {
      message.error("Failed to load plugins");
    } finally {
      setLoading(false);
    }
  };

  const handleInstall = async () => {
    if (!manifestJson.trim()) {
      message.error("Please provide manifest JSON");
      return;
    }

    setInstalling(true);
    try {
      const res = await fetch("/v1/vibe/plugins/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ manifest_json: manifestJson }),
      });

      const data = await res.json();

      if (data.status === "success") {
        message.success(`Plugin ${data.plugin_id} installed`);
        setManifestJson("");
        setModalVisible(false);
        loadPlugins();
      } else {
        message.error(data.message || "Installation failed");
      }
    } catch (err) {
      message.error("Install request failed");
    } finally {
      setInstalling(false);
    }
  };

  const handleDisable = async (pluginId: string) => {
    try {
      const res = await fetch(`/v1/vibe/plugins/${pluginId}/disable`, {
        method: "POST",
      });
      const data = await res.json();

      if (data.status === "success") {
        message.success(`Plugin ${pluginId} disabled`);
        loadPlugins();
      } else {
        message.error(data.message || "Disable failed");
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
            `/v1/vibe/plugins/${pluginId}/uninstall`,
            { method: "POST" }
          );
          const data = await res.json();

          if (data.status === "success") {
            message.success(`Plugin ${pluginId} uninstalled`);
            loadPlugins();
          } else {
            message.error(data.message || "Uninstall failed");
          }
        } catch (err) {
          message.error("Uninstall request failed");
        }
      },
    });
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
              icon={<PlusOutlined />}
              onClick={() => setModalVisible(true)}
            >
              Install Plugin
            </Button>
          </Space>
        }
      >
        <Tabs
          items={[
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

      <Modal
        title="Install Plugin"
        open={modalVisible}
        onOk={handleInstall}
        onCancel={() => {
          setModalVisible(false);
          setManifestJson("");
        }}
        confirmLoading={installing}
        width={600}
      >
        <div style={{ marginBottom: "16px" }}>
          <p>
            Paste plugin <code>plugin.json</code> manifest:
          </p>
          <Input.TextArea
            value={manifestJson}
            onChange={(e) => setManifestJson(e.target.value)}
            placeholder={`{
  "plugin": {
    "id": "my_plugin",
    "version": "1.0.0",
    "author": "you",
    "skills": [...]
  }
}`}
            rows={12}
            style={{ fontFamily: "monospace", fontSize: "12px" }}
          />
        </div>
      </Modal>
    </div>
  );
};

export default VibePluginsPanel;
