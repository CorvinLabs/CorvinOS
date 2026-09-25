/**
 * Skill Marketplace Panel — ADR-0682 Phase 6 k=3
 *
 * React component for skill discovery, search, and installation.
 * Full production UI with real API integration, detail modal, and progress tracking.
 */

import React, { useState, useEffect, useCallback } from "react";

interface SkillMetadata {
  skill_id: string;
  name: string;
  description: string;
  domain: string;
  tier: string;
  rating: number;
  install_count: number;
  version: string;
  tags: string[];
  created_at?: string;
  updated_at?: string;
  dependencies?: string[];
  matched_fields?: string[];
  relevance_score?: number;
}

interface SearchResponse {
  total: number;
  limit: number;
  offset: number;
  results: SkillMetadata[];
}

interface InstallStatus {
  job_id: string;
  skill_id: string;
  status: "pending" | "in_progress" | "completed" | "failed";
  progress: number;
  message: string;
}

// SearchBar Component
const SearchBar: React.FC<{ onSearch: (text: string) => void; loading: boolean }> = ({
  onSearch,
  loading,
}) => {
  const [searchText, setSearchText] = useState("");

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const text = e.target.value;
      setSearchText(text);
      onSearch(text);
    },
    [onSearch]
  );

  return (
    <div className="search-bar">
      <input
        type="text"
        placeholder="Search skills by name, description, or tags..."
        value={searchText}
        onChange={handleChange}
        disabled={loading}
        data-testid="skill-search-input"
      />
      {loading && <span className="search-loading">Searching...</span>}
    </div>
  );
};

// SkillCard Component
interface SkillCardProps {
  skill: SkillMetadata;
  onDetail: (skill: SkillMetadata) => void;
  onInstall: (skillId: string) => void;
  installing: boolean;
}

const SkillCard: React.FC<SkillCardProps> = ({
  skill,
  onDetail,
  onInstall,
  installing,
}) => (
  <div
    className="skill-card"
    onClick={() => onDetail(skill)}
    data-testid={`skill-card-${skill.skill_id}`}
  >
    <div className="skill-header">
      <h3>{skill.name}</h3>
      <span className="version">v{skill.version}</span>
    </div>
    <p className="description">{skill.description}</p>
    <div className="skill-meta">
      <span className="rating">⭐ {skill.rating.toFixed(1)}/5</span>
      <span className="tier" data-tier={skill.tier}>
        {skill.tier}
      </span>
      <span className="installs">📦 {skill.install_count}</span>
    </div>
    {skill.tags && skill.tags.length > 0 && (
      <div className="tags">
        {skill.tags.slice(0, 3).map((tag) => (
          <span key={tag} className="tag">
            {tag}
          </span>
        ))}
        {skill.tags.length > 3 && (
          <span className="tag-more">+{skill.tags.length - 3}</span>
        )}
      </div>
    )}
    <button
      className="install-btn"
      onClick={(e) => {
        e.stopPropagation();
        onInstall(skill.skill_id);
      }}
      disabled={installing}
      data-testid={`install-btn-${skill.skill_id}`}
    >
      {installing ? "Installing..." : "Install"}
    </button>
  </div>
);

// DetailModal Component
interface DetailModalProps {
  skill: SkillMetadata | null;
  onClose: () => void;
  onInstall: (skillId: string) => void;
  installing: boolean;
}

const SkillDetailModal: React.FC<DetailModalProps> = ({
  skill,
  onClose,
  onInstall,
  installing,
}) => {
  if (!skill) return null;

  return (
    <div className="modal-overlay" onClick={onClose} data-testid="skill-detail-modal">
      <div
        className="modal-content"
        onClick={(e) => e.stopPropagation()}
        data-testid={`detail-modal-${skill.skill_id}`}
      >
        <button className="modal-close" onClick={onClose}>
          ✕
        </button>
        <div className="modal-header">
          <h2>{skill.name}</h2>
          <span className="version">v{skill.version}</span>
        </div>
        <p className="description">{skill.description}</p>
        <div className="details">
          <div className="detail-row">
            <span className="label">Domain:</span>
            <span className="value">{skill.domain}</span>
          </div>
          <div className="detail-row">
            <span className="label">Tier:</span>
            <span className="value" data-tier={skill.tier}>
              {skill.tier}
            </span>
          </div>
          <div className="detail-row">
            <span className="label">Rating:</span>
            <span className="value">⭐ {skill.rating.toFixed(1)}/5</span>
          </div>
          <div className="detail-row">
            <span className="label">Installs:</span>
            <span className="value">{skill.install_count}</span>
          </div>
          {skill.created_at && (
            <div className="detail-row">
              <span className="label">Created:</span>
              <span className="value">
                {new Date(skill.created_at).toLocaleDateString()}
              </span>
            </div>
          )}
        </div>
        {skill.dependencies && skill.dependencies.length > 0 && (
          <div className="dependencies">
            <h4>Dependencies:</h4>
            <ul>
              {skill.dependencies.map((dep) => (
                <li key={dep}>{dep}</li>
              ))}
            </ul>
          </div>
        )}
        {skill.tags && skill.tags.length > 0 && (
          <div className="tags-detail">
            {skill.tags.map((tag) => (
              <span key={tag} className="tag">
                {tag}
              </span>
            ))}
          </div>
        )}
        <button
          className="install-btn-modal"
          onClick={() => onInstall(skill.skill_id)}
          disabled={installing}
          data-testid="install-modal-btn"
        >
          {installing ? "Installing..." : "Install Skill"}
        </button>
      </div>
    </div>
  );
};

// Main Marketplace Component
export const SkillMarketplacePanel: React.FC = () => {
  const [skills, setSkills] = useState<SkillMetadata[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSkill, setSelectedSkill] = useState<SkillMetadata | null>(null);
  const [installing, setInstalling] = useState(false);
  const [installStatus, setInstallStatus] = useState<InstallStatus | null>(null);
  const [query, setQuery] = useState({
    text: "",
    sort_by: "relevance",
    limit: 50,
    offset: 0,
  });
  const [pagination, setPagination] = useState({ offset: 0, total: 0 });

  useEffect(() => {
    fetchSkills(query.text === "" ? "" : query.text, query.offset);
  }, [query.text, query.offset]);

  const fetchSkills = useCallback(
    async (searchText: string, offset: number = 0) => {
      setLoading(true);
      setError(null);
      try {
        const endpoint =
          searchText === ""
            ? `/v1/skills/marketplace/index?limit=${query.limit}&offset=${offset}`
            : `/v1/skills/marketplace/search?q=${encodeURIComponent(searchText)}&limit=${query.limit}&offset=${offset}&sort_by=${query.sort_by}`;

        const response = await fetch(endpoint);
        if (!response.ok) {
          throw new Error(`API error: ${response.status}`);
        }

        const data: SearchResponse = await response.json();
        setSkills(data.results);
        setPagination({ offset, total: data.total });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Search failed");
        setSkills([]);
      } finally {
        setLoading(false);
      }
    },
    [query.limit, query.sort_by]
  );

  const handleSearch = useCallback((text: string) => {
    setQuery((q) => ({ ...q, text, offset: 0 }));
  }, []);

  const handleInstall = useCallback(async (skillId: string) => {
    setInstalling(true);
    try {
      const response = await fetch(
        `/v1/skills/marketplace/${skillId}/install`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tenant_id: "_default" }),
        }
      );

      if (!response.ok) {
        throw new Error(`Install failed: ${response.status}`);
      }

      const data: InstallStatus = await response.json();
      setInstallStatus(data);

      const pollInterval = setInterval(async () => {
        const statusResponse = await fetch(
          `/v1/skills/marketplace/install/${data.job_id}`
        );
        if (statusResponse.ok) {
          const statusData: InstallStatus = await statusResponse.json();
          setInstallStatus(statusData);

          if (
            statusData.status === "completed" ||
            statusData.status === "failed"
          ) {
            clearInterval(pollInterval);
            setInstalling(false);
          }
        }
      }, 1000);

      setTimeout(() => clearInterval(pollInterval), 30000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Installation failed");
      setInstalling(false);
    }
  }, []);

  const handlePrevPage = useCallback(() => {
    const newOffset = Math.max(0, pagination.offset - query.limit);
    setQuery((q) => ({ ...q, offset: newOffset }));
  }, [pagination.offset, query.limit]);

  const handleNextPage = useCallback(() => {
    if (pagination.offset + query.limit < pagination.total) {
      setQuery((q) => ({ ...q, offset: q.offset + q.limit }));
    }
  }, [pagination.offset, pagination.total, query.limit]);

  const pageNumber = Math.floor(pagination.offset / query.limit) + 1;
  const totalPages = Math.ceil(pagination.total / query.limit);

  return (
    <div className="skill-marketplace" data-testid="marketplace-panel">
      <h1>Skill Marketplace</h1>

      <SearchBar onSearch={handleSearch} loading={loading} />

      {error && <div className="error-banner">{error}</div>}

      {installStatus && (
        <div className="install-progress">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${installStatus.progress}%` }}
            ></div>
          </div>
          <p>
            {installStatus.status === "completed"
              ? "✅ Installation completed"
              : installStatus.status === "failed"
                ? "❌ Installation failed"
                : `Installing... ${installStatus.progress}%`}
          </p>
        </div>
      )}

      {skills.length === 0 && !loading && query.text === "" && (
        <div className="empty-state">
          <p>Search for skills to get started</p>
        </div>
      )}

      {skills.length === 0 && !loading && query.text !== "" && (
        <div className="empty-state">
          <p>No skills found. Try a different search.</p>
        </div>
      )}

      {skills.length > 0 && (
        <>
          <div className="skill-grid" data-testid="skill-grid">
            {skills.map((skill) => (
              <SkillCard
                key={skill.skill_id}
                skill={skill}
                onDetail={setSelectedSkill}
                onInstall={handleInstall}
                installing={installing}
              />
            ))}
          </div>

          {totalPages > 1 && (
            <div className="pagination">
              <button
                onClick={handlePrevPage}
                disabled={pagination.offset === 0}
              >
                ← Previous
              </button>
              <span>
                Page {pageNumber} of {totalPages}
              </span>
              <button
                onClick={handleNextPage}
                disabled={pagination.offset + query.limit >= pagination.total}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}

      <SkillDetailModal
        skill={selectedSkill}
        onClose={() => setSelectedSkill(null)}
        onInstall={handleInstall}
        installing={installing}
      />

      <style>{`
        .skill-marketplace {
          padding: 20px;
          max-width: 1400px;
          margin: 0 auto;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto;
        }

        .skill-marketplace h1 {
          margin: 0 0 20px 0;
          font-size: 28px;
          font-weight: 600;
        }

        .search-bar {
          position: relative;
          margin-bottom: 30px;
        }

        .search-bar input {
          width: 100%;
          padding: 12px 16px;
          font-size: 16px;
          border: 2px solid #e0e0e0;
          border-radius: 8px;
        }

        .search-bar input:focus {
          outline: none;
          border-color: #007bff;
        }

        .search-loading {
          position: absolute;
          right: 16px;
          top: 50%;
          transform: translateY(-50%);
          font-size: 12px;
          color: #666;
        }

        .error-banner {
          background-color: #fee;
          border: 1px solid #fcc;
          color: #c33;
          padding: 12px;
          border-radius: 6px;
          margin-bottom: 20px;
          font-size: 14px;
        }

        .install-progress {
          background-color: #f0f9ff;
          border: 1px solid #bae6fd;
          padding: 16px;
          border-radius: 6px;
          margin-bottom: 20px;
        }

        .progress-bar {
          width: 100%;
          height: 6px;
          background-color: #e0e0e0;
          border-radius: 3px;
          overflow: hidden;
          margin-bottom: 8px;
        }

        .progress-fill {
          height: 100%;
          background-color: #10b981;
          transition: width 0.3s ease;
        }

        .skill-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 20px;
          margin-bottom: 30px;
        }

        .skill-card {
          border: 1px solid #e0e0e0;
          border-radius: 8px;
          padding: 16px;
          background: #ffffff;
          cursor: pointer;
          transition: all 0.2s ease;
        }

        .skill-card:hover {
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
          border-color: #007bff;
        }

        .skill-header {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 8px;
        }

        .skill-card h3 {
          margin: 0;
          font-size: 16px;
          font-weight: 600;
        }

        .version {
          font-size: 12px;
          color: #999;
        }

        .description {
          margin: 8px 0;
          font-size: 14px;
          color: #666;
          line-height: 1.4;
        }

        .skill-meta {
          display: flex;
          gap: 12px;
          font-size: 12px;
          color: #666;
          margin-bottom: 12px;
        }

        .tier {
          background-color: #f0f0f0;
          padding: 2px 6px;
          border-radius: 3px;
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
        }

        .tags {
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
          margin-bottom: 12px;
        }

        .tag {
          background-color: #f3f4f6;
          padding: 4px 8px;
          border-radius: 4px;
          font-size: 11px;
          color: #6b7280;
        }

        .install-btn {
          width: 100%;
          padding: 10px;
          background-color: #007bff;
          color: white;
          border: none;
          border-radius: 6px;
          font-size: 14px;
          font-weight: 500;
          cursor: pointer;
        }

        .install-btn:hover:not(:disabled) {
          background-color: #0056b3;
        }

        .install-btn:disabled {
          background-color: #ccc;
          cursor: not-allowed;
        }

        .empty-state {
          text-align: center;
          padding: 60px 20px;
          color: #999;
        }

        .pagination {
          display: flex;
          justify-content: center;
          align-items: center;
          gap: 20px;
          margin-top: 30px;
          font-size: 14px;
        }

        .pagination button {
          padding: 8px 16px;
          background-color: #f0f0f0;
          border: 1px solid #ddd;
          border-radius: 4px;
          cursor: pointer;
          font-size: 14px;
        }

        .pagination button:hover:not(:disabled) {
          background-color: #007bff;
          color: white;
          border-color: #007bff;
        }

        .pagination button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .modal-overlay {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background-color: rgba(0, 0, 0, 0.5);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
        }

        .modal-content {
          background-color: white;
          border-radius: 12px;
          padding: 24px;
          max-width: 600px;
          width: 90%;
          max-height: 80vh;
          overflow-y: auto;
          position: relative;
        }

        .modal-close {
          position: absolute;
          top: 16px;
          right: 16px;
          background: none;
          border: none;
          font-size: 24px;
          cursor: pointer;
          color: #999;
          padding: 0;
          width: 32px;
          height: 32px;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .modal-close:hover {
          color: #333;
        }

        .modal-header {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 12px;
          padding-right: 32px;
        }

        .modal-header h2 {
          margin: 0;
          font-size: 22px;
          font-weight: 600;
        }

        .details {
          display: flex;
          flex-direction: column;
          gap: 12px;
          margin: 20px 0;
          padding: 16px;
          background-color: #f9f9f9;
          border-radius: 6px;
        }

        .detail-row {
          display: flex;
          justify-content: space-between;
          font-size: 14px;
        }

        .detail-row .label {
          font-weight: 500;
          color: #666;
        }

        .dependencies {
          margin: 16px 0;
        }

        .dependencies h4 {
          margin: 0 0 8px 0;
          font-size: 14px;
          font-weight: 600;
        }

        .dependencies ul {
          margin: 0;
          padding-left: 20px;
          font-size: 14px;
        }

        .tags-detail {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          margin: 16px 0;
        }

        .tags-detail .tag {
          background-color: #e8f3ff;
          color: #0056b3;
          padding: 6px 12px;
          border-radius: 4px;
          font-size: 12px;
        }

        .install-btn-modal {
          width: 100%;
          padding: 12px;
          background-color: #007bff;
          color: white;
          border: none;
          border-radius: 6px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          margin-top: 16px;
        }

        .install-btn-modal:hover:not(:disabled) {
          background-color: #0056b3;
        }

        .install-btn-modal:disabled {
          background-color: #ccc;
          cursor: not-allowed;
        }
      `}</style>
    </div>
  );
};

export default SkillMarketplacePanel;
