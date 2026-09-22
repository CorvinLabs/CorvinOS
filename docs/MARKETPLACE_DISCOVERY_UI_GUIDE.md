# Marketplace Discovery UI Guide (Phase 1 Session 1)

User-friendly guide to the new Marketplace Discovery features in the CorvinOS Console.

## Overview

The Marketplace Discovery interface makes it easy to find, evaluate, and install skills from the Corvin Marketplace. Features include:

- **Search** — Find skills by name, description, author, or tags
- **Filter** — Narrow results by category, tier, rating, and more
- **Sort** — Order by relevance, downloads, rating, or recency
- **Collections** — Browse pre-curated skill bundles for common use cases
- **Details** — View comprehensive skill information, version history, and reviews

## Getting Started

### Accessing the Marketplace

1. Open the CorvinOS Console
2. Click **Marketplace** in the left sidebar
3. Select the **Browse** tab

### Searching for Skills

**Basic Search:**
1. Enter a keyword in the search box (e.g., "cost optimization")
2. Press Enter or click the search icon
3. Results appear instantly with relevance-ranked matches

**Search Tips:**
- Search matches the skill **name**, **description**, **author**, and **tags**
- Searches are **case-insensitive**
- **Partial matches** are supported (e.g., "cost" matches "Cost Analyzer")
- **Empty search** returns all skills

### Filtering Results

#### By Category

1. Click the **Category** dropdown
2. Select a category (e.g., "Learning", "Optimization", "Media")
3. Only skills in that category are shown
4. The dropdown displays the count of skills per category

#### By Tier

1. Click the **Tier** dropdown
2. Choose **Built-in** (maintained by Corvin Labs) or **Community** (contributed)
3. Built-in skills require no additional consent to enable
4. Community skills require explicit consent before enabling

#### By Minimum Rating

1. Click the **Min Rating** dropdown
2. Select a rating threshold (3+, 4+, 4.5+)
3. Only skills meeting the rating are shown

#### By Tags

1. Results include tag badges (e.g., "ml", "optimization")
2. Click a tag to filter by that tag
3. An active tag filter shows as a blue badge with an × to remove it

#### Combining Filters

- All filters work **together** (AND logic)
- Example: `category=Learning AND tier=buildin AND min_rating=4.0` shows only built-in learning skills rated 4+
- Combine filters to narrow down exactly what you need

### Sorting Results

1. Click the **Sort** dropdown
2. Choose an ordering:

| Sort Option | When to Use |
|-------------|------------|
| **Relevance** (default) | Free-text search; orders by match quality |
| **Rating** | Find the highest-rated skills first |
| **Most Downloaded** | See the most popular/stable skills |
| **Recently Updated** | Find actively maintained skills |
| **Name (A-Z)** | Browse alphabetically |

### Viewing Skill Details

1. Click the **eye icon** on any skill card
2. A detailed panel opens showing:

**Overview Tab:**
- Full description and long description
- Metrics: rating, downloads, success rate, latency
- License, category, tags
- External links (README, documentation, source code)

**Versions Tab:**
- Complete version history
- Release dates and notes for each version
- Download counts per version

**Reviews Tab:**
- User ratings and reviews (if available)
- Helpful feedback from other operators

**Technical Tab:**
- Dependencies (Python/Node packages)
- Minimum CorvinOS version required
- Boot layer (if applicable)
- SLA level (if applicable)

### Installing Skills

**From a Skill Card:**
1. Click **Install** on the skill card
2. Installation begins immediately
3. A progress bar shows the install phases:
   - Index check
   - Source resolution
   - Manifest validation
   - License check
   - Installation
4. Once complete, a confirmation message appears

**Install Requirements:**
- Sufficient disk space (checked before install)
- Python version compatibility (checked)
- No conflicting skills (checked)
- License tier compatibility (if applicable)

**After Installation:**
- The button changes to **Enable**
- Click **Enable** to activate the skill (requires consent for community skills)
- The skill appears in the **Installed** tab

### Exploring Collections

**Pre-Curated Bundles:**
1. On the **Browse** tab, scroll to the bottom (when no search is active)
2. You'll see **Curated Collections** with cards showing:
   - Collection name and icon
   - Description and target use case
   - Difficulty level (Beginner / Intermediate / Advanced)
   - Number of skills in the bundle
   - Estimated setup time

**Types of Collections:**
- **Optimization Expert** — Cost reduction and model routing (Advanced, 30 min)
- **Data Analyst** — Data processing and visualization (Intermediate, 20 min)
- **Content Creator** — Video and media production (Beginner, 15 min)
- **Security Focused** — Compliance and audit (Advanced, 45 min)
- **Starter Pack** — Essential skills for new installations (Beginner, 10 min)

**Using a Collection:**
1. Click **Browse Collection** on a collection card
2. The browse tab filters to show all skills in that collection
3. Install individual skills as needed, or install the entire bundle

### Managing Filters

**Clear Individual Filters:**
- Filters appear as badges at the top
- Click the **×** on any filter badge to remove it

**Clear All Filters:**
- Click the **Clear** button (appears when any filters are active)
- All filters reset and the full marketplace is shown again

### Pagination

**Browsing Multiple Pages:**
1. By default, 12 skills are shown per page
2. At the bottom, use **Previous** / **Next** buttons or page indicator
3. Pagination resets when you change filters or search terms

### Accessibility Features

- **Keyboard navigation:** All controls support Tab/Enter navigation
- **Screen reader support:** Skills are announced with name, author, rating
- **Responsive design:** Works on desktop, tablet, and mobile devices
- **High contrast mode:** Supported via system preferences

## Common Tasks

### Find the Highest-Rated Skills

1. Set **Min Rating** dropdown to **4+ stars**
2. Set **Sort** to **Rating**
3. Results show top-rated skills first

### Discover Community Skills

1. Set **Tier** to **Community**
2. Browse the available community-contributed skills
3. Note the blue "Community" badge indicating consent will be required

### Build a Data Pipeline

1. Search for "data" or "pipeline"
2. Filter by **Category: Data Processing**
3. Review skills in the **Data Analyst** collection
4. Install and enable the relevant skills

### Install Everything You Need

1. Browse a **Collection** that matches your use case
2. Click **Browse Collection**
3. Install skills one by one (or select multiple and batch-install)
4. Enable each skill (grant consent for community skills if needed)

## Tips & Tricks

### Smart Searching

- Use **noun-based searches**: "cost" instead of "reduce costs"
- **Combine search + filters**: Search "optimization" + filter by "learning" category
- **Browse by rating**: Set high minimum rating to find proven skills

### Skill Evaluation

- **Check the rating**: Skills with 4.5+ stars are generally very good
- **Read reviews**: User feedback is valuable
- **Check the version history**: Actively maintained skills have recent updates
- **Review dependencies**: Ensure your environment has required packages

### Workflow Optimization

- **Use collections**: They're curated for specific use cases
- **Start with built-in skills**: No consent needed; maintained by Corvin Labs
- **Try community skills**: They often innovate faster; you control the risk via consent

## Troubleshooting

### No Results for My Search

- **Try broader terms**: "cost" instead of "cost-optimizer"
- **Clear filters**: Maybe a filter is hiding results
- **Browse categories**: Use the category dropdown to discover skills

### Skill Won't Install

- **Check requirements**: Verify Python/Node version in the details panel
- **Check disk space**: Installation requires free space
- **Check license tier**: Some skills require higher license tiers
- **See the error message**: The install dialog shows the exact blocker

### Can't Find a Specific Skill

- **Search by author**: If you know who created it
- **Browse categories**: The full marketplace has ~50+ skills
- **Check collections**: Pre-curated bundles highlight the most popular

### Skill Install Hangs

- Check the progress bar (should show phases: index → resolve → manifest → license → install)
- If stuck >5 minutes: Check the console logs
- If stuck >30 seconds: Try refreshing the page and re-installing

## Support

For issues or feature requests:

- **GitHub Issues**: https://github.com/CorvinLabs/CorvinOS/issues
- **Documentation**: https://docs.corvinlabs.io/marketplace
- **Community**: https://corvin.community/marketplace

---

**Last Updated:** September 22, 2026 (Phase 1 Session 1)
