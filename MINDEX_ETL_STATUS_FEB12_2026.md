# MINDEX ETL Status - February 12, 2026

## Current Database Counts

| Data Type | Count | Source | Status |
|-----------|-------|--------|--------|
| **Taxa** | 43,621+ | GBIF, MycoBank | ✅ Growing |
| **Observations** | 10,000+ | GBIF Occurrences | ✅ Growing |
| **Compounds** | 0 | PubChem, ChemSpider | 🟡 Pending (later phase) |
| **Sequences** | 0 | GenBank | 🟡 Pending (later phase) |

## Active ETL Runner

The aggressive ETL runner is running continuously on VM 189 and pulling data from:

### Phase 1: Taxonomy (ACTIVE)
- ✅ GBIF Species - Working, pulling 100s of species/second
- 🟡 MycoBank - Next in queue
- 🟡 TheYeasts.org - Later
- 🟡 Fusarium.org - Later
- 🟡 Mushroom.World - Later
- ❌ iNaturalist - Down (503 downtime), being skipped

### Phase 2: Observations
- ✅ GBIF Occurrences - Working, 10,000+ observations synced
- ❌ iNaturalist - Down, skipped

### Phase 3: Genomes & Sequences
- 🟡 FungiDB - Pending
- 🟡 GenBank/NCBI - Pending

### Phase 4: Chemistry
- 🟡 PubChem - Pending
- 🟡 ChemSpider - Pending

### Phase 5-7: Publications, Media, Traits
- 🟡 All pending

### Phase 8: Web Scraping Fallback
- 🟡 Will activate if APIs fail

## How to Monitor

```bash
# SSH to VM 189
ssh mycosoft@192.168.0.189

# Check ETL logs
tail -f /home/mycosoft/mindex/etl.log

# Check database counts
docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) FROM core.taxon"
docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) FROM obs.observation"

# Check ETL process
ps aux | grep aggressive_runner
```

## Data Sources Being Scraped

1. **GBIF** - Global Biodiversity Information Facility
   - 150,000+ fungal species
   - Millions of occurrence records with GPS coordinates
   
2. **MycoBank** - 545,000+ fungal species names
   
3. **FungiDB** - Fungal genomes and annotations
   
4. **GenBank/NCBI** - Fungal sequences (ITS, LSU, SSU)
   
5. **PubChem** - Fungal compounds, mycotoxins
   
6. **ChemSpider** - Chemical structures
   
7. **TheYeasts.org** - Comprehensive yeast database
   
8. **Fusarium.org** - Fusarium species database
   
9. **Mushroom.World** - Mushroom species info
   
10. **Index Fungorum** - Nomenclature authority
    
11. **Wikipedia** - Species descriptions
    
12. **PubMed** - Research publications

## ETL Will Run Forever

The aggressive runner is configured to:
- Run continuously until manually stopped
- Cycle through all sources every ~2 minutes
- Skip failing sources (like iNaturalist 503)
- Retry rate-limited sources after 1 minute
- Fall back to web scraping when APIs fail

## Files Added/Modified

- `mindex_etl/aggressive_runner.py` - Main runner
- `mindex_etl/sources/genbank.py` - GenBank NCBI source
- `mindex_etl/sources/pubchem.py` - PubChem source
- `mindex_etl/jobs/sync_genbank_genomes.py` - GenBank sync job
- `mindex_etl/jobs/sync_pubchem_compounds.py` - PubChem sync job

## Expected Growth

With all sources running, expect:
- **100,000+ taxa** within 24 hours
- **500,000+ observations** within 48 hours
- **10,000+ compounds** within 24 hours
- **50,000+ sequences** within 24 hours
