/**
 * Plugin Trust & Governance Badge Component
 *
 * Displays plugin governance information:
 * - Trust level (builtin | vetted ✓ | community ⚠)
 * - Author information (if signed)
 * - Permissions (egress, PII risk)
 * - Ratings (read-only in MVP)
 *
 * ADR-0249: Plugin Trust Anchor — displays origin + permissions
 */

import React, { useState } from "react";
import {
  Card,
  Tag,
  Space,
  Tooltip,
  Button,
  Modal,
  Form,
  Input,
  Select,
  message,
  Divider,
  Row,
  Col,
  Alert,
  Rate,
  Spin,
} from "antd";
import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  WarningOutlined,
  FlagOutlined,
  LinkOutlined,
  GlobalOutlined,
  SecurityScanOutlined,
  UserOutlined,
} from "@ant-design/icons";

interface PluginGovernanceInfo {
  origin: "builtin" | "vetted" | "community";
  author?: string;
  author_url?: string;
  pii_risk: "none" | "low" | "medium" | "high";
  locality: "local" | "eu_cloud" | "us_cloud" | "unknown";
  network_egress: "none" | "local" | "external";
  egress_hosts?: string[];
  requires_consent: boolean;
  rating?: number; // 0-5 stars (read-only in MVP)
  report_count?: number;
}

interface PluginTrustBadgeProps {
  plugin_id: string;
  governance: PluginGovernanceInfo;
  onReport?: (plugin_id: string, reason: string, details: string) => Promise<void>;
  isLoading?: boolean;
}

const trustLevelConfig = {
  builtin: {
    color: "blue",
    icon: <CheckCircleOutlined />,
    label: "Builtin",
    description: "Ships with CorvinOS",
  },
  vetted: {
    color: "green",
    icon: <CheckCircleOutlined />,
    label: "Vetted ✓",
    description: "Reviewed by maintainer",
  },
  community: {
    color: "orange",
    icon: <WarningOutlined />,
    label: "Community ⚠",
    description: "Unreviewed third-party",
  },
};

const piiRiskConfig = {
  none: { color: "green", label: "No PII" },
  low: { color: "blue", label: "Low Risk" },
  medium: { color: "orange", label: "Medium Risk" },
  high: { color: "red", label: "High Risk" },
};

const localityConfig = {
  local: { icon: "🏠", label: "Local", description: "Runs locally" },
  eu_cloud: { icon: "🇪🇺", label: "EU Cloud", description: "EU infrastructure" },
  us_cloud: { icon: "🇺🇸", label: "US Cloud", description: "US infrastructure" },
  unknown: { icon: "❓", label: "Unknown", description: "Not classified" },
};

const egressConfig = {
  none: { icon: "🔒", label: "No Egress", description: "Air-gapped" },
  local: { icon: "🔗", label: "Local Only", description: "Local network only" },
  external: { icon: "🌐", label: "Internet", description: "Public internet" },
};

/**
 * Trust Badge: displays origin with icon and tooltip
 */
const TrustBadge: React.FC<{ origin: string }> = ({ origin }) => {
  const config =
    trustLevelConfig[origin as keyof typeof trustLevelConfig] ||
    trustLevelConfig.community;

  return (
    <Tooltip title={config.description}>
      <Tag color={config.color} icon={config.icon}>
        {config.label}
      </Tag>
    </Tooltip>
  );
};

/**
 * Permissions Disclosure: shows data locality, egress, PII risk
 */
const PermissionsDisclosure: React.FC<{
  locality: string;
  network_egress: string;
  egress_hosts?: string[];
  pii_risk: string;
}> = ({ locality, network_egress, egress_hosts, pii_risk }) => {
  const locConfig = localityConfig[locality as keyof typeof localityConfig] || localityConfig.unknown;
  const egressCfg = egressConfig[network_egress as keyof typeof egressConfig] || egressConfig.external;
  const piiCfg = piiRiskConfig[pii_risk as keyof typeof piiRiskConfig] || piiRiskConfig.high;

  return (
    <Card size="small" title="Permissions & Declarations" style={{ marginTop: 16 }}>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12}>
          <Space direction="vertical" size="small" style={{ display: "block" }}>
            <div>
              <strong>Data Locality</strong>
              <br />
              <span style={{ fontSize: "12px", color: "#666" }}>
                {locConfig.icon} {locConfig.label} — {locConfig.description}
              </span>
            </div>
          </Space>
        </Col>
        <Col xs={24} sm={12}>
          <Space direction="vertical" size="small" style={{ display: "block" }}>
            <div>
              <strong>Network Egress</strong>
              <br />
              <span style={{ fontSize: "12px", color: "#666" }}>
                {egressCfg.icon} {egressCfg.label} — {egressCfg.description}
              </span>
            </div>
          </Space>
        </Col>
        <Col xs={24} sm={12}>
          <Space direction="vertical" size="small" style={{ display: "block" }}>
            <div>
              <strong>PII Risk</strong>
              <br />
              <Tag
                color={piiCfg.color}
                style={{ marginTop: "4px" }}
              >
                {piiCfg.label}
              </Tag>
            </div>
          </Space>
        </Col>
        {egress_hosts && egress_hosts.length > 0 && (
          <Col xs={24}>
            <Space direction="vertical" size="small" style={{ display: "block" }}>
              <div>
                <strong>Allowed Hosts</strong>
                <br />
                <div style={{ fontSize: "12px", color: "#666", marginTop: "4px" }}>
                  {egress_hosts.map((host) => (
                    <div key={host}>
                      <code>{host}</code>
                    </div>
                  ))}
                </div>
              </div>
            </Space>
          </Col>
        )}
      </Row>
    </Card>
  );
};

/**
 * Author Information: displays signer details if available
 */
const AuthorInfo: React.FC<{ author?: string; author_url?: string; origin: string }> = ({
  author,
  author_url,
  origin,
}) => {
  if (!author && origin !== "community") {
    return null;
  }

  return (
    <Card size="small" title="Author & Signature" style={{ marginTop: 16 }}>
      <Space>
        <UserOutlined />
        {author ? (
          <>
            <span>{author}</span>
            {author_url && (
              <a href={author_url} target="_blank" rel="noopener noreferrer">
                <LinkOutlined />
              </a>
            )}
          </>
        ) : (
          <span style={{ color: "#999" }}>Not signed (community plugin)</span>
        )}
      </Space>
    </Card>
  );
};

/**
 * Report Plugin Modal: allows flagging inappropriate/malicious plugins
 */
const ReportPluginModal: React.FC<{
  visible: boolean;
  plugin_id: string;
  onClose: () => void;
  onSubmit: (reason: string, details: string) => Promise<void>;
  loading: boolean;
}> = ({ visible, plugin_id, onClose, onSubmit, loading }) => {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = React.useState(false);

  const handleSubmit = async (values: any) => {
    try {
      setSubmitting(true);
      await onSubmit(values.reason, values.details);
      message.success("Report submitted. Thank you for reporting this plugin.");
      form.resetFields();
      onClose();
    } catch (err) {
      message.error(`Failed to submit report: ${err}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="Report Plugin"
      open={visible}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={submitting}
      okText="Submit Report"
      cancelText="Cancel"
    >
      <Alert
        message="Community & Vetted Plugins"
        description={`You are reporting: ${plugin_id}`}
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
      />
      <Form form={form} layout="vertical" onFinish={handleSubmit}>
        <Form.Item
          name="reason"
          label="Report Reason"
          rules={[{ required: true, message: "Please select a reason" }]}
        >
          <Select
            placeholder="Select a reason for reporting"
            options={[
              {
                value: "malicious",
                label: "Malicious Activity (steals data, injects code, etc.)",
              },
              {
                value: "inappropriate",
                label: "Inappropriate Content",
              },
              {
                value: "permission_abuse",
                label: "Excessive Permissions",
              },
              {
                value: "misrepresentation",
                label: "Misrepresented Capabilities",
              },
              {
                value: "other",
                label: "Other",
              },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="details"
          label="Details"
          rules={[
            { required: true, message: "Please provide details" },
            { min: 10, message: "Please provide at least 10 characters" },
            { max: 500, message: "Maximum 500 characters" },
          ]}
        >
          <Input.TextArea
            placeholder="Describe the issue in detail (10-500 characters)"
            rows={4}
            maxLength={500}
            showCount
          />
        </Form.Item>
      </Form>
    </Modal>
  );
};

/**
 * Main Plugin Trust Badge Component
 */
export const PluginTrustBadge: React.FC<PluginTrustBadgeProps> = ({
  plugin_id,
  governance,
  onReport,
  isLoading = false,
}) => {
  const [reportVisible, setReportVisible] = useState(false);
  const [reportSubmitting, setReportSubmitting] = useState(false);

  const handleReportSubmit = async (reason: string, details: string) => {
    if (!onReport) {
      message.error("Report functionality not available");
      return;
    }

    try {
      setReportSubmitting(true);
      await onReport(plugin_id, reason, details);
    } finally {
      setReportSubmitting(false);
    }
  };

  return (
    <>
      <Card
        title={
          <Space>
            <SecurityScanOutlined />
            Plugin Governance & Trust
          </Space>
        }
        loading={isLoading}
        extra={
          governance.origin === "community" || governance.origin === "vetted" ? (
            <Button
              danger
              size="small"
              icon={<FlagOutlined />}
              onClick={() => setReportVisible(true)}
            >
              Report
            </Button>
          ) : null
        }
      >
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          {/* Trust Level */}
          <div>
            <strong>Trust Level</strong>
            <br />
            <TrustBadge origin={governance.origin} />
            {governance.origin === "community" && governance.requires_consent && (
              <Alert
                message="Requires Explicit Consent"
                description="You must explicitly enable this plugin to use it"
                type="warning"
                showIcon
                style={{ marginTop: 12 }}
              />
            )}
          </div>

          <Divider style={{ margin: "12px 0" }} />

          {/* Author Info */}
          {(governance.author || governance.origin === "community") && (
            <AuthorInfo
              author={governance.author}
              author_url={governance.author_url}
              origin={governance.origin}
            />
          )}

          {/* Permissions */}
          <PermissionsDisclosure
            locality={governance.locality}
            network_egress={governance.network_egress}
            egress_hosts={governance.egress_hosts}
            pii_risk={governance.pii_risk}
          />

          {/* Rating (read-only in MVP) */}
          {governance.rating !== undefined && (
            <Card size="small" style={{ marginTop: 16 }}>
              <Space>
                <span>Community Rating:</span>
                <Rate disabled value={governance.rating} />
                {governance.report_count !== undefined && (
                  <span style={{ fontSize: "12px", color: "#999" }}>
                    ({governance.report_count} reports)
                  </span>
                )}
              </Space>
            </Card>
          )}
        </Space>
      </Card>

      {/* Report Modal */}
      <ReportPluginModal
        visible={reportVisible}
        plugin_id={plugin_id}
        onClose={() => setReportVisible(false)}
        onSubmit={handleReportSubmit}
        loading={reportSubmitting}
      />
    </>
  );
};

export default PluginTrustBadge;
