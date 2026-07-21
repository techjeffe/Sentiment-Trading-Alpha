# News & Filings View - Implementation Plan (UPDATED)

## ✅ Completed Items

### Phase 1: Backend (COMPLETE)
- [x] Add retention config to `logic_config.json`
- [x] Create `cleanup_old_filings()` in `edgar_worker.py`
- [x] Create unified news API endpoint (`routers/news.py`)
- [x] Register router in `main.py`
- [x] Test API endpoints

### Phase 2: Frontend (COMPLETE)
- [x] Create News page (`frontend/app/news/page.tsx`)
- [x] Create NewsFilter component (`frontend/app/news/components/NewsFilter.tsx`)
- [x] Create NewsList component (`frontend/app/news/components/NewsList.tsx`)
- [x] Create NewsDetail component (`frontend/app/news/components/NewsDetail.tsx`)
- [x] Add News link to main navigation (`frontend/src/app/page.tsx`)

### Phase 3: Documentation (COMPLETE)
- [x] Update README.md
- [x] Update RELEASENOTES.md
- [x] Update NEWS_VIEW_PLAN.md

---

## 📋 Remaining Tasks

### Testing
- [ ] Test backend API endpoints (after restart)
- [ ] Test frontend News page (after restart)
- [ ] Verify data retention cleanup works
- [ ] Run full system test

### Optional Enhancements (Future)
- [ ] Add "mark as read" functionality
- [ ] Add export to CSV/JSON
- [ ] Add real-time updates (WebSocket)
- [ ] Improve article-to-symbol mapping (currently articles don't have direct symbol field)

---

## 🔧 Implementation Details

### Backend API

**Endpoint:** `GET /api/v1/news`

**Query Parameters:**
- `symbol` (optional): Filter by symbol
- `start_date` (optional): Filter by date range start
- `end_date` (optional): Filter by date range end
- `source` (optional): Filter by source (`rss`, `truth_social`, `web_research`, `edgar`)
- `limit` (default: 50)
- `offset` (default: 0)

**Response:**
```json
{
  "total": 245,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "id": "...",
      "symbol": "NVDA",
      "source": "edgar",
      "source_label": "SEC EDGAR",
      "title": "8-K filing (2026-07-02)",
      "summary": "NVIDIA executive leadership change...",
      "published_at": "2026-07-02T16:32:00Z",
      "url": "https://www.sec.gov/...",
      "processed": true,
      "details": {
        "form_type": "8-K",
        "items": "5.02",
        "llm_summary": "..."
      }
    }
  ]
}
```

### Frontend UI

**Page:** `/news`

**Components:**
1. **NewsFilter** - Symbol, date range, source type filters
2. **NewsList** - Cards with source icon, symbol, title, date
3. **NewsDetail** - Expandable modal with full text + LLM summary

**Navigation:** Added to main tab navigation in `page.tsx`

---

## 🚀 Data Retention Policy

**Configuration:** `logic_config.json` → `data_retention`

| Data Type | Retention | Notes |
|-----------|------------|-------|
| RSS articles | 30 days | Configurable |
| Truth Social posts | 7 days | Configurable |
| SEC EDGAR filings | 90 days | Configurable |
| Processed items | 30 days | Prune after |

**Cleanup API:** `POST /api/v1/news/cleanup`

---

## 📝 Next Steps

1. **Test the implementation:**
   ```powershell
   # Restart backend
   cd backend
   python run.py
   
   # Test API endpoints
   Invoke-RestMethod -Uri "http://localhost:8000/api/v1/news" -Method GET
   ```

2. **Push to GitHub:**
   ```bash
   git add .
   git commit -m "feat: Add News & Filings unified view + EDGAR integration"
   git push
   ```

3. **Update documentation** (completed)

---

## 🐛 Known Issues

1. **Articles don't have direct symbol mapping** - Currently filtered by symbol only for EDGAR filings
2. **Double-prefix routes** - Fixed in both edgar and news routers
3. **Unicode encoding** - Fixed arrow character in engine.py print statement

---

**Last Updated:** January 20, 2027
**Status:** Phase 1 & 2 Complete, Ready for Testing
