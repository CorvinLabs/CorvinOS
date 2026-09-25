/**
 * Skill Marketplace Panel — ADR-0682 Phase 6 k=2
 *
 * React component for skill discovery, search, and installation.
 * Stub for k=2; full UI implementation in k=3.
 */

import React, { useState, useEffect } from "react";

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
}

interface SearchQuery {
  text: string;
  domain?: string;
  tier?: string;
  sort_by: string;
  limit: number;
  offset: number;
}

export const SkillMarketplacePanel: React.FC = () => {
  const [skills, setSkills] = useState<SkillMetadata[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState<SearchQuery>({
    text: "",
    sort_by: "relevance",
    limit: 50,
    offset: 0,
  });

  useEffect(() => {
    // Stub: fetch skills from API in k=3
    console.log("Skill Marketplace Panel loaded (stub for k=2)");
  }, []);

  const handleSearch = async (text: string) => {
    setLoading(true);
    try {
      // Stub: call /v1/skills/marketplace/search in k=3
      console.log("Search triggered:", text);
    } catch (error) {
      console.error("Search failed:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleInstall = async (skillId: string) => {
    try {
      // Stub: call POST /v1/skills/marketplace/{skillId}/install in k=3
      console.log("Install triggered:", skillId);
    } catch (error) {
      console.error("Install failed:", error);
    }
  };

  return (
    <div className="skill-marketplace">
      <h1>Skill Marketplace</h1>

      {/* SearchBar Component (k=3) */}
      <div className="search-bar">
        <input
          type="text"
          placeholder="Search skills..."
          onChange={(e) => handleSearch(e.target.value)}
          disabled={loading}
        />
        <p>{loading ? "Loading..." : "Enter search query"}</p>
      </div>

      {/* SkillGrid Component (k=3) */}
      <div className="skill-grid">
        {skills.length === 0 && !loading && (
          <p>No skills found. Try searching (UI fully implemented in k=3).</p>
        )}
        {skills.map((skill) => (
          <div key={skill.skill_id} className="skill-card">
            <h3>{skill.name}</h3>
            <p>{skill.description}</p>
            <div className="skill-meta">
              <span className="rating">⭐ {skill.rating}/5</span>
              <span className="installs">{skill.install_count} installs</span>
            </div>
            <button onClick={() => handleInstall(skill.skill_id)}>
              Install
            </button>
          </div>
        ))}
      </div>

      {/* DetailModal Component (k=3) */}
      {/* <SkillDetailModal skill={selectedSkill} onClose={handleCloseDetail} /> */}

      {/* InstallProgressOverlay Component (k=3) */}
      {/* <InstallProgressOverlay jobId={currentInstall?.job_id} /> */}

      <style jsx>{`
        .skill-marketplace {
          padding: 20px;
          max-width: 1200px;
          margin: 0 auto;
        }

        .search-bar {
          margin-bottom: 30px;
        }

        .search-bar input {
          width: 100%;
          padding: 10px;
          font-size: 16px;
          border: 1px solid #ddd;
          border-radius: 4px;
        }

        .skill-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
          gap: 20px;
        }

        .skill-card {
          border: 1px solid #e0e0e0;
          border-radius: 8px;
          padding: 16px;
          background: #f9f9f9;
        }

        .skill-card h3 {
          margin: 0 0 8px 0;
        }

        .skill-meta {
          display: flex;
          gap: 10px;
          margin-bottom: 10px;
          font-size: 14px;
          color: #666;
        }

        .skill-card button {
          width: 100%;
          padding: 8px;
          background: #007bff;
          color: white;
          border: none;
          border-radius: 4px;
          cursor: pointer;
        }

        .skill-card button:hover {
          background: #0056b3;
        }
      `}</style>
    </div>
  );
};

export default SkillMarketplacePanel;
