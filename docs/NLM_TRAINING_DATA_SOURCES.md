# NLM TRAINING DATA SOURCES — CURSOR INTEGRATION REFERENCE
## Mycosoft / Zeetachec TAC-O Maritime Acoustic Intelligence
### Feed this entire file into Cursor alongside TACO_CURSOR_IMPLEMENTATION_PLAN.md

---

## OVERVIEW

This document is the machine-readable data source registry for the Neural Listening Machine (NLM).
Every dataset, pre-trained model, and software tool below maps directly to NLM subsystem training tasks.
Use this to build data ingestion pipelines, training scripts, and evaluation benchmarks.

**Companion docs:**
- `TACO_CURSOR_IMPLEMENTATION_PLAN.md` — repo map, execution lanes, code templates
- `zeetachec_mycosoft_taco_plan.md` — full teaming plan and requirements

---

## REPO MAP (from implementation plan)

| Repo | GitHub Path | Role |
|---|---|---|
| `NLM` | `MycosoftLabs/NLM` | Foundation model — acoustic classification, anomaly detection, prediction heads |
| `mindex` | `MycosoftLabs/mindex` | Data platform — maritime schemas, ETL pipelines, vector search, Worldview API |
| `mycosoft-mas` | `MycosoftLabs/mycosoft-mas` | MAS orchestrator, FUSARIUM API, CREP streaming, decision support |
| `mycobrain` | `MycosoftLabs/mycobrain` | Edge firmware — signal processing, acoustic modem, Jetson integration |
| `website` | `MycosoftLabs/website` | CREP dashboard, FUSARIUM dashboard, defense pages |

---

## PYTHON DEPENDENCIES

```bash
# Core audio ML stack
pip install torch torchaudio librosa soundfile scipy numpy pandas
pip install datasets transformers huggingface_hub  # HuggingFace ecosystem
pip install speechbrain  # BEATs and audio ML framework
pip install netCDF4 xarray  # oceanographic data (NetCDF)
pip install h5py  # HDF5 support

# Data download utilities
pip install kaggle  # Kaggle datasets
pip install gdown  # Google Drive downloads
pip install boto3  # AWS S3 (Pacific Sound / MBARI)
pip install requests beautifulsoup4  # Web fetching
```

---

## DATA SOURCE REGISTRY

Each entry follows this schema:
```
id:          unique key for code references
name:        human-readable name
url:         primary access URL
type:        data type
format:      file format(s)
size:        approximate size
access:      open | academic | restricted | api
nlm_target:  which NLM subsystem this feeds
repo:        which Mycosoft repo consumes this
priority:    P0 (immediate) | P1 (week 1) | P2 (week 2-4) | P3 (backlog)
loader:      Python snippet or command to access
```

---

## SECTION 1: UNDERWATER ACOUSTIC DATABASES
**NLM Target**: `nlm/data/ambient/` — Ambient ocean baseline, anomaly detection training
**Repo**: `MycosoftLabs/NLM`, `MycosoftLabs/mindex`

### 1.1 NOAA Ocean Noise Reference Station Network (NRS)
- **id**: `noaa_nrs`
- **url**: https://www.ncei.noaa.gov/maps/passive-acoustic-data/
- **type**: Continuous hydrophone recordings
- **format**: WAV / HDF5
- **size**: Petabytes (multi-year continuous)
- **access**: open
- **nlm_target**: ambient baseline, shipping noise, weather noise classification
- **priority**: P0

```python
# NOAA NRS — access via NCEI passive acoustic data API
# Browse interactively: https://www.ncei.noaa.gov/maps/passive-acoustic-data/
# Bulk data: https://www.ncei.noaa.gov/access/passive-acoustic-data/
import requests

NCEI_PAD_BASE = "https://www.ncei.noaa.gov/access/passive-acoustic-data/"
# Select specific station and date range from the interactive map
# Download WAV files directly via HTTPS
```

### 1.2 MBARI Open Acoustic Data (Pacific Sound)
- **id**: `mbari_pacific_sound`
- **url**: https://www.mbari.org/project/open-acoustic-data/
- **aws**: https://registry.opendata.aws/pacific-sound/
- **type**: Broadband hydrophone recordings
- **format**: WAV (16-bit, high sample rate)
- **size**: ~2 TB/month per sensor
- **access**: open (AWS S3)
- **nlm_target**: deep ocean ambient, biological sounds, anthropogenic noise
- **priority**: P0

```python
# MBARI Pacific Sound — open on AWS S3
import boto3
from botocore import UNSIGNED
from botocore.config import Config

s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
bucket = 'pacific-sound'

# List available data
response = s3.list_objects_v2(Bucket=bucket, Prefix='', Delimiter='/')
for prefix in response.get('CommonPrefixes', []):
    print(prefix['Prefix'])

# Download a specific file
# s3.download_file(bucket, 'path/to/file.wav', 'local_file.wav')
```

### 1.3 NOAA-Navy SanctSound (Sanctuary Soundscapes)
- **id**: `sanctsound`
- **url**: https://sanctsound.ioos.us/
- **ncei**: https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncei.pad%3ANOAA-Navy-SanctSound_Raw_Data
- **type**: Passive acoustic + AIS + environmental metadata
- **format**: WAV / CSV
- **size**: Multi-TB, 30 sites, 4 years (2018-2022)
- **access**: open
- **nlm_target**: multi-modal training (acoustic + AIS correlation), marine sanctuary sounds
- **priority**: P0

### 1.4 IQOE Acoustic Data Portal
- **id**: `iqoe_portal`
- **url**: https://www.iqoe.org/acoustic-data-portal
- **type**: Meta-portal to global PAM datasets
- **format**: Various
- **size**: Index to hundreds of datasets worldwide
- **access**: open (portal — individual dataset access varies)
- **nlm_target**: discovery of additional international datasets
- **priority**: P2

### 1.5 UK Acoustics Network Open Access Data Directory
- **id**: `uk_acoustics_directory`
- **url**: https://acoustics.ac.uk/open-access-underwater-acoustics-data/
- **type**: Curated meta-directory of 50+ underwater acoustic datasets
- **format**: Various
- **size**: Links to 50+ datasets
- **access**: open
- **nlm_target**: dataset discovery, additional labelled and unlabelled sources
- **priority**: P2

### 1.6 NCEI Passive Acoustic Data Archive
- **id**: `ncei_pad`
- **url**: https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncei.pad%3APAD_Collection
- **type**: National PAM archive (NRS + SanctSound + regional)
- **format**: WAV / HDF5
- **size**: Petabytes
- **access**: open
- **nlm_target**: comprehensive PAM data
- **priority**: P1

### 1.7 Ocean Networks Canada (ONC)
- **id**: `onc`
- **url**: https://www.oceannetworks.ca/
- **type**: Continuous multi-sensor ocean data (hydrophones + CTD + currents)
- **format**: WAV, CSV, NetCDF
- **size**: Petabytes (decades)
- **access**: open (CC-BY-ND, registration required)
- **nlm_target**: multi-sensor fusion training, NE Pacific / Arctic / NW Atlantic
- **priority**: P2

### 1.8 EMSO-ERIC Observatory Network
- **id**: `emso`
- **url**: https://emso.eu/observatories/
- **type**: European seafloor observatory data (14 sites)
- **format**: Various
- **size**: Multi-TB
- **access**: open (varies by observatory)
- **nlm_target**: European waters baseline, multi-sensor data
- **priority**: P3

### 1.9 IOOS Regional Ocean Observing Systems
- **id**: `ioos`
- **url**: https://ioos.us/regions
- **type**: Regional PAM + oceanographic data (US Pacific / Atlantic / Hawaii)
- **format**: Various
- **size**: Multi-TB
- **access**: open (US federally funded)
- **nlm_target**: US coastal acoustic baseline
- **priority**: P2

### 1.10 Galway Bay Observatory Hydrophone
- **id**: `galway_bay`
- **url**: https://data.gov.ie/dataset/galway-bay-observatory-hydrophone-raw-data
- **type**: Raw hydrophone recordings (200 kHz omni-directional)
- **format**: WAV
- **size**: Multi-year continuous
- **access**: open (CC license)
- **nlm_target**: coastal hydrophone training data
- **priority**: P3

### 1.11 MobySound / CIMRS
- **id**: `mobysound`
- **url**: https://www.mobysound.org/index.html
- **type**: Long-term PAM (equatorial Pacific)
- **format**: WAV
- **size**: Multi-year datasets
- **access**: open
- **nlm_target**: equatorial ocean baseline
- **priority**: P3

### 1.12 Glacier Bay Underwater Sounds (NPS)
- **id**: `glacier_bay`
- **url**: https://www.nps.gov/glba/learn/nature/soundclips.htm
- **type**: Labelled underwater sound clips (whales, seals, vessels, weather)
- **format**: WAV / MP3
- **size**: Small (curated clips)
- **access**: open
- **nlm_target**: labelled multi-class samples for initial classifier prototyping
- **priority**: P1

### 1.13 NOAA Ocean Explorer Sea Sounds
- **id**: `noaa_seasounds`
- **url**: https://oceanexplorer.noaa.gov/explorations/sound01/background/seasounds/seasounds.html
- **type**: Curated ocean sound reference (geological, biological, weather)
- **format**: Audio clips
- **size**: Small
- **access**: open
- **nlm_target**: reference labels for ambient sound taxonomy
- **priority**: P2

---

## SECTION 2: VESSEL / SUBMARINE ACOUSTIC SIGNATURES
**NLM Target**: `nlm/models/vessel_classifier/` — Ship identification, UATR
**Repo**: `MycosoftLabs/NLM`

### 2.1 ShipsEar
- **id**: `shipsear`
- **url**: https://www.sciencedirect.com/science/article/abs/pii/S0003682X16301566
- **type**: Labelled ship-radiated noise (11 vessel categories)
- **format**: WAV
- **size**: 90 recordings, 11 classes
- **access**: academic (via publication request)
- **nlm_target**: vessel classification baseline, UATR benchmark
- **priority**: P0

### 2.2 DeepShip
- **id**: `deepship`
- **url**: https://www.emergentmind.com/topics/deepship-and-shipsear-benchmarks
- **type**: Ship noise spectrograms + raw audio (4 vessel types)
- **format**: WAV / spectrogram images
- **size**: 265 recordings, 4 classes
- **access**: academic
- **nlm_target**: deep learning UATR training, spectrogram classifier
- **priority**: P0

### 2.3 DS3500 (Enhanced ShipsEar)
- **id**: `ds3500`
- **url**: https://huggingface.co/datasets/peng7554/DS3500
- **type**: Real + simulated ship noise with distance/depth labels
- **format**: Audio + CSV metadata
- **size**: 3,500 samples
- **access**: open (HuggingFace)
- **nlm_target**: multi-task UATR (classification + localization)
- **priority**: P0

```python
# DS3500 — direct HuggingFace download
from datasets import load_dataset

ds = load_dataset("peng7554/DS3500")
print(ds)
# Access audio and labels directly
# train_sample = ds['train'][0]
```

### 2.4 QiandaoEar22
- **id**: `qiandaoear22`
- **url**: https://arxiv.org/html/2406.04354v1
- **type**: Vessel-specific noise signatures (individual vessel ID)
- **format**: WAV
- **size**: Research dataset
- **access**: academic (via publication)
- **nlm_target**: fine-grained vessel identification
- **priority**: P1

### 2.5 HearMyShip
- **id**: `hearmyship`
- **url**: https://www.nature.com/articles/s41597-025-04584-x
- **type**: Small vessel underwater radiated noise
- **format**: WAV + metadata
- **size**: Research dataset (2025)
- **access**: open (Scientific Data)
- **nlm_target**: small craft acoustic signatures, coastal vessel ID
- **priority**: P1

### 2.6 Wolfset
- **id**: `wolfset`
- **url**: https://pmc.ncbi.nlm.nih.gov/articles/PMC12311032/
- **type**: Multi-class underwater acoustic targets
- **format**: WAV
- **size**: Research dataset
- **access**: academic
- **nlm_target**: UATR benchmarking, multi-class evaluation
- **priority**: P2

### 2.7 Kaggle Underwater Acoustic Signal Modulation Recognition
- **id**: `kaggle_uasmr`
- **url**: https://www.kaggle.com/competitions/underwater-acoustic-signal-modulation-recognition/data
- **type**: Simulated underwater acoustic signals
- **format**: Various
- **size**: Competition dataset
- **access**: open (CC-BY, Kaggle)
- **nlm_target**: signal modulation classification
- **priority**: P2

```bash
# Kaggle dataset download (requires kaggle CLI configured)
kaggle competitions download -c underwater-acoustic-signal-modulation-recognition
```

---

## SECTION 3: MARINE MAMMAL & BIOLOGICAL SOUNDS
**NLM Target**: `nlm/models/bio_classifier/` — Species ID, biological source separation
**Repo**: `MycosoftLabs/NLM`

### 3.1 Watkins Marine Mammal Sound Database (WHOI)
- **id**: `watkins_whoi`
- **url**: https://www.whoi.edu/press-room/news-release/historic-marine-mammal-sound-archive-now-available-online/
- **github**: https://github.com/mopg/getWHOIdata
- **type**: Labelled marine mammal vocalizations (clicks, whistles, pulsed calls)
- **format**: WAV / MP3
- **size**: 1,700+ recordings, 60+ species
- **access**: open
- **nlm_target**: marine mammal species classification, biological noise separation
- **priority**: P0

```python
# Watkins/WHOI — use GitHub helper tool
# git clone https://github.com/mopg/getWHOIdata.git
# cd getWHOIdata && python get_data.py

# Or direct access via WHOI web interface
# https://cis.whoi.edu/science/B/whalesounds/index.cfm
```

### 3.2 DCLDE Workshop Datasets
- **id**: `dclde`
- **url**: https://www.cetus.ucsd.edu/dclde/datasetDocumentation.html
- **type**: Annotated marine mammal recordings for benchmarking
- **format**: WAV + annotation files
- **size**: Multi-TB across workshop years (2011, 2013, 2015, 2024)
- **access**: workshop-specific (some openly available)
- **nlm_target**: detection/classification/localization/density estimation benchmark
- **priority**: P1

### 3.3 NOAA Fisheries Marine Mammal Acoustics
- **id**: `noaa_fisheries_mma`
- **url**: https://www.fisheries.noaa.gov/new-england-mid-atlantic/science-data/marine-mammal-acoustics
- **type**: Marine mammal detection and monitoring data
- **format**: Various
- **size**: Large (multi-program)
- **access**: open
- **nlm_target**: operational marine mammal detection, right whale monitoring
- **priority**: P1

### 3.4 Cornell Macaulay Library
- **id**: `macaulay`
- **url**: https://www.macaulaylibrary.org/
- **type**: Natural history audio/video (marine mammals, fish, invertebrates + terrestrial)
- **format**: Various audio formats
- **size**: Millions of recordings
- **access**: open (Cornell Lab of Ornithology)
- **nlm_target**: broadest species coverage for biological sound classification
- **priority**: P1

### 3.5 DTIC Marine Animal Sound Database
- **id**: `dtic_marine_sounds`
- **url**: https://apps.dtic.mil/sti/tr/pdf/ADA244694.pdf
- **type**: Military-compiled marine biological acoustic signatures
- **format**: PDF reference / archived data
- **size**: Reference document
- **access**: open (DTIC public)
- **nlm_target**: DoD-validated marine bio reference, Navy-aligned taxonomy
- **priority**: P2

### 3.6 FishSounds.net
- **id**: `fishsounds`
- **url**: https://fishsounds.net/
- **type**: Fish and invertebrate vocalizations
- **format**: Audio clips + metadata
- **size**: Growing database
- **access**: open
- **nlm_target**: non-mammal biological sound classification
- **priority**: P2

---

## SECTION 4: AERIAL / DRONE / BIRD ACOUSTIC
**NLM Target**: `nlm/models/aerial_classifier/` — Above-surface domain awareness
**Repo**: `MycosoftLabs/NLM`

### 4.1 DroneAudioset
- **id**: `droneaudioset`
- **url**: https://www.emergentmind.com/topics/droneaudioset
- **type**: Labelled drone acoustic signatures (multiple types, distances, conditions)
- **format**: WAV + metadata
- **size**: Research dataset
- **access**: academic
- **nlm_target**: UAV acoustic detection and classification
- **priority**: P1

### 4.2 32-Category Drone / UAV Sound Dataset
- **id**: `uav_32cat`
- **url**: https://dael.euracoustics.org/confs/fa2023/data/articles/000049.pdf
- **type**: Multi-class UAV recordings (32 categories, flight conditions, maneuvers)
- **format**: WAV
- **size**: 32 categories
- **access**: academic
- **nlm_target**: fine-grained drone model identification
- **priority**: P2

### 4.3 Xeno-canto Bird Sound Database
- **id**: `xenocanto`
- **url**: https://xeno-canto.org/
- **huggingface**: https://huggingface.co/datasets/ilyassmoummad/Xeno-Canto-6s-16khz
- **gbif**: https://www.gbif.org/dataset/b1047888-ae52-4179-9dd5-5448ea342a24
- **type**: Bird vocalizations by species (10,000+ species)
- **format**: MP3 / WAV
- **size**: 684,000+ recordings
- **access**: open (CC)
- **nlm_target**: bird vs drone discrimination, aerial biological baseline
- **priority**: P1

```python
# Xeno-canto via HuggingFace (pre-segmented 6s clips at 16kHz)
from datasets import load_dataset

xc = load_dataset("ilyassmoummad/Xeno-Canto-6s-16khz")
print(f"Total samples: {len(xc['train'])}")

# Or use Xeno-canto API directly
import requests
# Search for a species
r = requests.get("https://xeno-canto.org/api/2/recordings", params={"query": "Phoebastria nigripes"})
data = r.json()
```

### 4.4 NAVFAC Aircraft Sound Monitoring
- **id**: `navfac_aircraft`
- **url**: https://www.navfac.navy.mil/Directorates/Public-Works/Products-and-Services/Aircraft-Sound-Monitoring/
- **type**: Military aircraft noise monitoring data
- **format**: Monitoring reports / data files
- **size**: Program-specific
- **access**: restricted (DoD/NAVFAC)
- **nlm_target**: military aircraft acoustic signatures
- **priority**: P3 (requires Navy contracting vehicle via Zeetachec)

### 4.5 SERDP Military Aircraft Noise Propagation
- **id**: `serdp_aircraft`
- **url**: https://serdp-estcp.mil/projects/details/09cc2b4b-1d53-4b9c-b91a-f8d006279de5
- **type**: Acoustic propagation models + validation data
- **format**: Model outputs / research data
- **access**: DoD research (SERDP/ESTCP)
- **nlm_target**: aerial acoustic propagation modeling
- **priority**: P3

---

## SECTION 5: EXPLOSION / WEAPON / MILITARY ACOUSTIC
**NLM Target**: `nlm/models/threat_classifier/` — Threat detection and alerting
**Repo**: `MycosoftLabs/NLM`

### 5.1 UXO Acoustic-Optical Dataset
- **id**: `uxo_zenodo`
- **url**: https://zenodo.org/records/11068046
- **type**: Multi-modal UXO detection (acoustic + optical)
- **format**: Audio + image
- **size**: Research dataset
- **access**: open (Zenodo, CC)
- **nlm_target**: underwater munitions/threat detection
- **priority**: P1

```bash
# Download from Zenodo
wget https://zenodo.org/records/11068046/files/<filename> -P data/uxo/
```

### 5.2 IOGP Underwater Explosions as Acoustic Sources
- **id**: `iogp_explosions`
- **url**: https://usrd.iogp.org/resource/underwater-explosions-as-acoustic-sources/
- **type**: Explosion signature characterization reference
- **format**: Publication / reference data
- **access**: open
- **nlm_target**: explosion acoustic signature reference model
- **priority**: P2

### 5.3 Shallow Underwater Explosions Recordings
- **id**: `shallow_explosions`
- **url**: https://pubs.geoscienceworld.org/ssa/bssa/article/113/4/1542/618917/
- **type**: Short-range shallow underwater explosion waveforms
- **format**: Seismic / acoustic time series
- **access**: academic (SSA publication)
- **nlm_target**: explosion waveform templates
- **priority**: P2

---

## SECTION 6: ENVIRONMENTAL SOUND CLASSIFICATION (TRANSFER LEARNING)
**NLM Target**: `nlm/pretrain/` — Pre-training and transfer learning base
**Repo**: `MycosoftLabs/NLM`

### 6.1 Google AudioSet
- **id**: `audioset`
- **url**: https://research.google.com/audioset/
- **download**: https://research.google.com/audioset/download.html
- **github**: https://github.com/IvanBirkmaier/Audioset
- **type**: Multi-label audio events (2M+ clips, 600+ categories)
- **format**: TFRecord embeddings (2.4 GB) or WAV via YouTube
- **size**: 2+ million 10-second clips
- **access**: open
- **nlm_target**: foundation pre-training for PANNs/BEATs/AST, transfer learning base
- **priority**: P0 (pre-training dependency)

```python
# AudioSet embeddings (fastest — pre-computed 128-D features)
# Download: https://research.google.com/audioset/download.html
# balanced_train_segments.csv, unbalanced_train_segments.csv, eval_segments.csv
# Feature embeddings: audioset_v1_embeddings/ (TFRecord files)

# To download raw audio from YouTube IDs:
# pip install yt-dlp
# yt-dlp -x --audio-format wav -o "%(id)s.%(ext)s" <youtube_id>
```

### 6.2 ESC-50 (Environmental Sound Classification)
- **id**: `esc50`
- **url**: https://github.com/karolpiczak/ESC-50
- **huggingface**: https://huggingface.co/datasets/ashraq/esc50
- **type**: 2,000 environmental sounds in 50 classes
- **format**: WAV (5-second clips)
- **size**: 2,000 clips, 50 classes
- **access**: open
- **nlm_target**: environmental sound classification benchmark, includes rain/sea_waves/engine/helicopter
- **priority**: P0

```python
# ESC-50 via HuggingFace
from datasets import load_dataset
esc50 = load_dataset("ashraq/esc50")

# Or clone from GitHub
# git clone https://github.com/karolpiczak/ESC-50.git
# Audio files in ESC-50/audio/
# Metadata in ESC-50/meta/esc50.csv
```

### 6.3 UrbanSound8K
- **id**: `urbansound8k`
- **url**: https://urbansounddataset.weebly.com/urbansound8k.html
- **huggingface**: https://huggingface.co/datasets/MahiA/UrbanSound8K
- **type**: 8,732 urban sound excerpts (10 classes: engine, siren, gunshot, etc.)
- **format**: WAV (up to 4s)
- **size**: 8,732 clips
- **access**: open (academic license)
- **nlm_target**: transfer learning baseline, engine/machinery sound recognition
- **priority**: P1

### 6.4 FSD50K
- **id**: `fsd50k`
- **url**: https://zenodo.org/records/4060432
- **type**: 51,197 audio clips, 200 AudioSet classes, human-verified
- **format**: WAV + JSON annotations
- **size**: 51,197 clips
- **access**: open (CC-BY)
- **nlm_target**: fine-grained audio event classification
- **priority**: P1

```bash
# FSD50K — download from Zenodo
wget https://zenodo.org/records/4060432/files/FSD50K.dev_audio.zip
wget https://zenodo.org/records/4060432/files/FSD50K.eval_audio.zip
wget https://zenodo.org/records/4060432/files/FSD50K.ground_truth.zip
```

### 6.5 Freesound.org
- **id**: `freesound`
- **url**: https://freesound.org/
- **type**: Community audio samples (500K+ sounds)
- **format**: WAV / MP3 / FLAC / OGG
- **size**: 500,000+ sounds
- **access**: open (CC licenses, free account)
- **nlm_target**: supplementary training data, underwater/engine/weather recordings
- **priority**: P2

```python
# Freesound API
import freesound
client = freesound.FreesoundClient()
client.set_token("<your_api_key>", "token")
results = client.text_search(query="underwater", filter="tag:ocean")
```

### 6.6 BBC Sound Effects
- **id**: `bbc_sfx`
- **url**: https://sound-effects.bbcrewind.co.uk/
- **type**: Professional sound effects (33K+ effects)
- **format**: WAV
- **size**: 33,000+ effects
- **access**: free (personal/educational/research)
- **nlm_target**: training data augmentation
- **priority**: P3

### 6.7 DCASE Challenge Datasets
- **id**: `dcase`
- **url**: https://dcase.community/challenge2024/index
- **type**: Multi-task audio classification benchmarks (9 tasks annually)
- **format**: WAV + annotations
- **size**: Thousands of clips per task
- **access**: open (challenge participants)
- **nlm_target**: acoustic scene classification, anomalous sound detection, bioacoustic events
- **priority**: P2

---

## SECTION 7: NOAA / NASA / GOVERNMENT OCEANOGRAPHIC DATA
**NLM Target**: `nlm/data/environment/`, `mindex/schemas/oceanographic/`
**Repo**: `MycosoftLabs/mindex`, `MycosoftLabs/NLM`

### 7.1 World Ocean Atlas (WOA) Sound Speed Profiles
- **id**: `woa_soundspeed`
- **url**: https://staff.washington.edu/dushaw/WOA/
- **noaa**: https://www.ncei.noaa.gov/access/world-ocean-atlas-2023/
- **type**: Global climatological T/S/sound-speed profiles
- **format**: NetCDF / ASCII
- **size**: Global gridded data
- **access**: open
- **nlm_target**: acoustic propagation modeling, ray tracing, transmission loss prediction
- **priority**: P0

```python
# WOA Sound Speed Profiles
import xarray as xr

# Download WOA23 temperature and salinity NetCDF files
# Then compute sound speed using UNESCO/Chen-Millero equation
# Or use pre-computed profiles from: https://staff.washington.edu/dushaw/WOA/

def sound_speed_chen_millero(T, S, D):
    """Chen-Millero equation for sound speed in seawater.
    T: temperature (°C), S: salinity (PSU), D: depth (m)
    Returns: sound speed (m/s)
    """
    c0 = 1402.388 + 5.03830*T - 5.81090e-2*T**2 + 3.3432e-4*T**3
    c0 += -1.47797e-6*T**4 + 3.1419e-9*T**5
    c0 += (0.153563 + 6.8999e-4*T - 8.1829e-6*T**2 + 1.3632e-7*T**3 - 6.1260e-10*T**4) * D
    c0 += (3.1260e-5 - 1.7111e-6*T + 2.5986e-8*T**2 + -2.5353e-10*T**3 + 1.0415e-12*T**4) * D**2
    c0 += (-9.7729e-9 + 3.8513e-10*T - 2.3654e-12*T**2) * D**3
    c0 += 1.389 * (S - 35.0) + (-1.262e-2 + 7.166e-5*T + 2.008e-6*T**2 - 3.21e-8*T**3) * (S - 35.0)
    return c0
```

### 7.2 Global Undersea Acoustic Parameters Dataset
- **id**: `global_acoustic_params`
- **url**: https://pmc.ncbi.nlm.nih.gov/articles/PMC11605126/
- **type**: Pre-computed sound channel axis, critical depth, convergence zone ranges
- **format**: NetCDF / CSV
- **size**: Global gridded
- **access**: open
- **nlm_target**: acoustic propagation parameter lookup (convergence zones, shadow zones)
- **priority**: P1

### 7.3 NDBC (National Data Buoy Center)
- **id**: `ndbc`
- **url**: https://www.ndbc.noaa.gov/
- **type**: Wind, wave, temperature, pressure, current data (1,400+ stations)
- **format**: CSV / text / NetCDF
- **size**: Decades of data
- **access**: open
- **nlm_target**: ambient noise condition prediction, wind-driven noise modeling
- **priority**: P0

```python
# NDBC real-time and historical data
import pandas as pd

# Station metadata
stations = pd.read_csv("https://www.ndbc.noaa.gov/data/stations/station_table.txt",
                        sep="|", skiprows=2)

# Historical data for specific station (e.g., 46025 off Santa Monica)
url = "https://www.ndbc.noaa.gov/view_text_file.php?filename=46025h2024.txt.gz&dir=data/historical/stdmet/"
# Parse fixed-width format
```

### 7.4 NAVOCEANO MOODS
- **id**: `navoceano_moods`
- **url**: https://catalog.data.gov/dataset/temperature-salinity-and-sound-speed-profile-data-from-the-navoceano-master-oceanographic-obser
- **type**: Navy T/S/sound-speed profiles
- **format**: Various
- **size**: Comprehensive Navy archive
- **access**: open (public subset via Data.gov)
- **nlm_target**: Navy-validated environmental data for sonar performance prediction
- **priority**: P1 (request full access via Zeetachec contracting vehicle)

### 7.5 Copernicus Marine Service
- **id**: `copernicus_marine`
- **url**: https://data.marine.copernicus.eu/
- **type**: Global/regional ocean products (SST, salinity, currents, sea level)
- **format**: NetCDF
- **size**: Petabytes
- **access**: open (free registration)
- **nlm_target**: global ocean state for acoustic environment prediction
- **priority**: P1

```python
# Copernicus Marine — use copernicusmarine Python library
# pip install copernicusmarine
import copernicusmarine

# Download sea surface temperature
copernicusmarine.subset(
    dataset_id="cmems_mod_glo_phy_my_0.083deg_P1D-m",
    variables=["thetao"],
    minimum_longitude=-80, maximum_longitude=-60,
    minimum_latitude=25, maximum_latitude=45,
    start_datetime="2024-01-01", end_datetime="2024-01-31",
    minimum_depth=0, maximum_depth=5000,
    output_filename="copernicus_temp.nc"
)
```

### 7.6 NASA Earthdata Ocean Portal
- **id**: `nasa_earthdata`
- **url**: https://www.earthdata.nasa.gov/topics/ocean
- **type**: Satellite-derived SST, ocean color, SSH, wind, sea ice
- **format**: HDF5 / NetCDF / GeoTIFF
- **size**: Petabytes
- **access**: open (NASA Earthdata login)
- **nlm_target**: satellite-derived environmental context
- **priority**: P2

---

## SECTION 8: BATHYMETRY & SEAFLOOR TERRAIN
**NLM Target**: `nlm/data/environment/bathymetry/`, `mindex/schemas/terrain/`
**Repo**: `MycosoftLabs/mindex`

### 8.1 GEBCO 2025 Global Bathymetric Grid
- **id**: `gebco_2025`
- **url**: https://www.gebco.net/data-products/gridded-bathymetry-data
- **download**: https://download.gebco.net
- **type**: Global ocean + land terrain (15 arc-second resolution)
- **format**: NetCDF (~4 GB) / GeoTIFF (~8 GB)
- **size**: ~4-8 GB per grid
- **access**: open
- **nlm_target**: acoustic bottom interaction modeling, bathymetric context for ray tracing
- **priority**: P0

```python
# GEBCO 2025 — load with xarray
import xarray as xr

gebco = xr.open_dataset("GEBCO_2025.nc")
elevation = gebco['elevation']  # positive = land, negative = ocean depth

# Subset to region of interest
region = elevation.sel(lat=slice(25, 45), lon=slice(-80, -60))
print(f"Depth range: {float(region.min())}m to {float(region.max())}m")
```

### 8.2 IBCAO (Arctic Ocean)
- **id**: `ibcao`
- **url**: https://www.gebco.net/data-products/gridded-bathymetry-data/arctic-ocean
- **type**: Arctic bathymetry (north of 64°N)
- **format**: NetCDF / GeoTIFF
- **access**: open (via GEBCO)
- **nlm_target**: Arctic underwater operations terrain
- **priority**: P3

### 8.3 IBCSO (Southern Ocean)
- **id**: `ibcso`
- **url**: https://ibcso.org/
- **type**: Southern Ocean bathymetry
- **format**: NetCDF / GeoTIFF
- **access**: open
- **nlm_target**: Southern Ocean operations terrain
- **priority**: P3

---

## SECTION 9: MAGNETOMETER & MAGNETIC ANOMALY DETECTION (MAD)
**NLM Target**: `nlm/models/mad_detector/`, `nlm/data/magnetic/`
**Repo**: `MycosoftLabs/NLM`

### 9.1 World Magnetic Model 2025 (WMM2025)
- **id**: `wmm2025`
- **url**: https://www.ncei.noaa.gov/products/world-magnetic-model
- **type**: Global geomagnetic field model (DoD/NATO standard)
- **format**: Model coefficients (WMM.COF) + software
- **size**: Small (model coefficients)
- **access**: open
- **nlm_target**: magnetic baseline for anomaly detection (subtract Earth's field)
- **priority**: P0

```python
# WMM2025 — use wmm2020 Python package (update coefficients for 2025)
# pip install wmm2020
import wmm2020

# Get magnetic field at a point
mag = wmm2020.wmm(lat=32.7, lon=-117.2, alt_km=0, yeardec=2026.3)
print(f"Total field: {mag.total:.1f} nT")
print(f"Declination: {mag.decl:.2f}°")
print(f"Inclination: {mag.incl:.2f}°")
```

### 9.2 EMAG2v3 (Earth Magnetic Anomaly Grid)
- **id**: `emag2v3`
- **url**: https://www.ncei.noaa.gov/products/earth-magnetic-model-anomaly-grid-2
- **doi**: https://doi.org/10.7289/V5H70CVX
- **type**: Global magnetic anomaly grid (2 arc-minute, satellite+ship+airborne)
- **format**: CSV (1.5 GB zip) / GeoTIFF (175 MB)
- **size**: 1.5 GB
- **access**: open
- **nlm_target**: crustal magnetic anomaly baseline for MAD submarine detection
- **priority**: P0

```bash
# EMAG2v3 — download GeoTIFF (smallest)
wget https://www.ncei.noaa.gov/data/earth-magnetic-model-anomaly-grid/access/EMAG2v3/EMAG2_V3_20170530.tif
# Or CSV version
wget https://www.ncei.noaa.gov/data/earth-magnetic-model-anomaly-grid/access/EMAG2v3/EMAG2_V3_20170530.csv.zip
```

```python
# Load EMAG2v3 GeoTIFF
import rasterio
import numpy as np

with rasterio.open("EMAG2_V3_20170530.tif") as src:
    anomaly_grid = src.read(1)  # magnetic anomaly in nT
    transform = src.transform
    print(f"Grid shape: {anomaly_grid.shape}")
    print(f"Anomaly range: {np.nanmin(anomaly_grid):.1f} to {np.nanmax(anomaly_grid):.1f} nT")
```

### 9.3 Data.gov Magnetic Anomaly Datasets (14 datasets)
- **id**: `datagov_magnetic`
- **url**: https://catalog.data.gov/dataset/?tags=magnetic+anomalies
- **type**: Aeromagnetic surveys, marine magnetic profiles, regional compilations
- **format**: Various (CSV, XYZ, shapefiles)
- **size**: 14 datasets
- **access**: open
- **nlm_target**: regional magnetic survey data for training/validation
- **priority**: P2

### 9.4 MAID Dataset (ML-based Magnetic Anomaly Interpolation)
- **id**: `maid`
- **url**: https://academic.oup.com/gji/article/245/2/ggag076/8494940
- **type**: Magnetic anomaly data + ML interpolation methods
- **access**: academic
- **nlm_target**: data gap filling for sparse magnetic surveys
- **priority**: P3

### 9.5 2D Magnetometer Network for Underwater Intrusion Detection
- **id**: `mag_intrusion`
- **url**: https://pmc.ncbi.nlm.nih.gov/articles/PMC12899529/
- **type**: Magnetometer network + AI for underwater intrusion detection (2025)
- **access**: open (PMC)
- **nlm_target**: reference architecture for NLM magnetic sensing subsystem
- **priority**: P1

---

## SECTION 10: AIS / SATELLITE / GEOSPATIAL MARITIME
**NLM Target**: `mindex/schemas/maritime_traffic/`, `nlm/data/groundtruth/`
**Repo**: `MycosoftLabs/mindex`

### 10.1 NOAA AccessAIS
- **id**: `noaa_ais`
- **url**: https://coast.noaa.gov/digitalcoast/tools/ais.html
- **type**: Historical and near-real-time AIS vessel positions (US waters)
- **format**: CSV / database queries
- **size**: Billions of position reports
- **access**: open
- **nlm_target**: acoustic-AIS correlation, ground truth for vessel detection
- **priority**: P0

```python
# NOAA AIS — download via Digital Coast
# Interactive: https://coast.noaa.gov/digitalcoast/tools/ais.html
# Or use MarineCadastre for bulk (see 10.4)

# For real-time AIS streaming:
# https://ais.coast.noaa.gov/
```

### 10.2 Ushant AIS Traffic Dataset
- **id**: `ushant_ais`
- **url**: https://github.com/rtavenar/ushant_ais
- **type**: Curated vessel trajectories (18.7M position reports)
- **format**: CSV (lat/lon/time/MMSI/SOG/COG)
- **size**: 18.7 million reports
- **access**: open
- **nlm_target**: trajectory prediction, vessel behavior modeling, anomaly detection
- **priority**: P1

```bash
git clone https://github.com/rtavenar/ushant_ais.git
```

### 10.3 Global Maritime Traffic Density
- **id**: `global_maritime_traffic`
- **url**: https://globalmaritimetraffic.org
- **type**: Worldwide AIS traffic density maps
- **format**: Raster maps / data files
- **access**: varies
- **nlm_target**: traffic pattern context for acoustic environment prediction
- **priority**: P2

### 10.4 MarineCadastre.gov AIS Data
- **id**: `marinecadastre`
- **url**: https://marinecadastre.gov/ais/
- **type**: Monthly nationwide US AIS data (since 2009)
- **format**: CSV / GDB
- **size**: Terabytes
- **access**: open (US government)
- **nlm_target**: bulk historical AIS for training vessel-acoustic correlation
- **priority**: P1

---

## SECTION 11: PRE-TRAINED ML MODELS & WEIGHTS
**NLM Target**: `nlm/pretrain/`, `nlm/models/`
**Repo**: `MycosoftLabs/NLM`

### 11.1 PANNs (Large-Scale Pretrained Audio Neural Networks)
- **id**: `panns`
- **url**: https://github.com/qiuqiangkong/audioset_tagging_cnn
- **paper**: https://arxiv.org/abs/1912.10211
- **type**: CNN14, ResNet38, etc. pre-trained on AudioSet (527 classes)
- **format**: PyTorch weights (.pth)
- **size**: ~300 MB per model
- **access**: open
- **nlm_target**: BACKBONE — primary audio feature extractor, fine-tune for all NLM classifiers
- **priority**: P0 (CRITICAL DEPENDENCY)

```python
# PANNs — load pre-trained CNN14 for feature extraction
# git clone https://github.com/qiuqiangkong/audioset_tagging_cnn.git

import torch
# Download CNN14 checkpoint
# wget https://zenodo.org/records/3987831/files/Cnn14_mAP%3D0.431.pth

# Load model
from pytorch.models import Cnn14
model = Cnn14(sample_rate=32000, window_size=1024, hop_size=320,
              mel_bins=64, fmin=50, fmax=14000, classes_num=527)
checkpoint = torch.load("Cnn14_mAP=0.431.pth", map_location='cpu')
model.load_state_dict(checkpoint['model'])
model.eval()

# Extract embeddings for fine-tuning
# embedding = model.forward(waveform)['embedding']  # 2048-D vector
```

### 11.2 BEATs (Audio Pre-Training with Acoustic Tokenizers)
- **id**: `beats`
- **url**: https://github.com/microsoft/unilm/tree/master/beats
- **paper**: https://www.microsoft.com/en-us/research/publication/beats-audio-pre-training-with-acoustic-tokenizers/
- **type**: Audio transformer (bidirectional encoder, iterative pre-training)
- **format**: PyTorch checkpoints (~90 MB)
- **access**: open (MIT license)
- **nlm_target**: BACKBONE ALTERNATIVE — state-of-the-art audio classification, SpeechBrain integration
- **priority**: P0

```python
# BEATs via SpeechBrain
# pip install speechbrain
from speechbrain.inference import EncoderClassifier

classifier = EncoderClassifier.from_hparams(
    source="speechbrain/BEATs",
    savedir="pretrained_models/beats"
)

# Classify audio file
output = classifier.classify_file("audio_sample.wav")
print(output)
```

### 11.3 AST (Audio Spectrogram Transformer)
- **id**: `ast`
- **url**: https://github.com/YuanGongND/ast
- **type**: Vision Transformer applied to audio spectrograms
- **format**: PyTorch weights (~300 MB)
- **access**: open
- **nlm_target**: alternative backbone, strong on AudioSet benchmarks
- **priority**: P1

### 11.4 PANN_Models_DeepShip (Underwater Transfer Learning)
- **id**: `panns_deepship`
- **url**: https://github.com/doans/Underwater-Acoustic-Target-Classification-Based-on-Dense-Convolutional-Neural-Network
- **type**: PANNs + Dense CNNs fine-tuned on DeepShip/ShipsEar
- **format**: PyTorch code + weights
- **access**: open
- **nlm_target**: UATR-specific fine-tuned model — start here for vessel classification
- **priority**: P0

### 11.5 UWTRL-MEG (Underwater Target Recognition & Localization)
- **id**: `uwtrl_meg`
- **url**: https://huggingface.co/peng7554/UWTRL-MEG
- **type**: Underwater acoustic recognition + range/depth estimation models
- **format**: PyTorch model weights
- **access**: open (HuggingFace)
- **nlm_target**: multi-task underwater model (classify + locate)
- **priority**: P0

```python
# UWTRL-MEG from HuggingFace
from huggingface_hub import hf_hub_download

model_path = hf_hub_download(repo_id="peng7554/UWTRL-MEG", filename="model.pth")
```

### 11.6 Fish Sound Classifier
- **id**: `fish_classifier`
- **url**: https://huggingface.co/axds/classify-fish-sounds
- **type**: Pre-trained fish sound classification model
- **access**: open (HuggingFace)
- **nlm_target**: marine biological sound classification
- **priority**: P2

### 11.7 underwater_snd
- **id**: `underwater_snd`
- **url**: https://github.com/lucascesarfd/underwater_snd
- **type**: Underwater sound classification framework
- **format**: Python / PyTorch
- **access**: open
- **nlm_target**: training pipeline reference for underwater domains
- **priority**: P2

### 11.8 Faster R-CNN Marine Mammal Detection
- **id**: `frcnn_marine`
- **url**: https://tethys.pnnl.gov/publications/deep-learning-model-detecting-classifying-multiple-marine-mammal-species-passive
- **type**: Faster R-CNN for marine mammal spectrogram detection
- **access**: academic (via Tethys/publication)
- **nlm_target**: spectrogram object detection for bio source ID
- **priority**: P2

---

## SECTION 12: SONAR IMAGE & TARGET DETECTION
**NLM Target**: `nlm/models/sonar_detector/`
**Repo**: `MycosoftLabs/NLM`

### 12.1 OpenSonarDatasets (Master Directory)
- **id**: `opensonar`
- **url**: https://github.com/remaro-network/OpenSonarDatasets
- **type**: Meta-directory of 20+ open sonar datasets
- **access**: open
- **nlm_target**: discovery of sonar imagery datasets
- **priority**: P1

### 12.2 UATD (Underwater Acoustic Target Detection Dataset)
- **id**: `uatd`
- **url**: https://www.nature.com/articles/s41597-022-01854-w
- **type**: 9,200+ multibeam FLS images (Tritech Gemini 1200ik)
- **format**: BMP + Pascal VOC XML annotations
- **size**: 9,200 images
- **access**: open (Figshare)
- **nlm_target**: forward-looking sonar target detection
- **priority**: P1

### 12.3 SCTD (Sonar Common Target Detection Dataset)
- **id**: `sctd`
- **url**: https://github.com/freepoet/SCTD
- **type**: Sonar target detection (Pascal VOC + COCO converter)
- **access**: open
- **nlm_target**: sonar object detection training
- **priority**: P2

### 12.4 Sonar Object Detection (Roboflow)
- **id**: `roboflow_sonar`
- **url**: https://universe.roboflow.com/datasetad/sonar-zsqwb
- **type**: 7,848 FLS sonar images annotated for object detection
- **format**: YOLO / COCO / Pascal VOC / TFRecord
- **size**: 7,848 images
- **access**: open
- **nlm_target**: ready-to-train sonar detection (YOLO-compatible)
- **priority**: P1

```python
# Roboflow sonar dataset — multiple export formats available
# pip install roboflow
from roboflow import Roboflow
rf = Roboflow(api_key="<your_key>")
project = rf.workspace("datasetad").project("sonar-zsqwb")
dataset = project.version(1).download("yolov8")
```

---

## SECTION 13: PAM SOFTWARE & FRAMEWORKS
**NLM Target**: Integration with NLM inference pipeline
**Repo**: `MycosoftLabs/NLM`, `MycosoftLabs/mycosoft-mas`

### 13.1 PAMGuard
- **id**: `pamguard`
- **url**: https://www.pamguard.org/
- **type**: Open-source PAM platform (detection, classification, localization, density estimation)
- **format**: Java (cross-platform)
- **access**: open source (free)
- **nlm_target**: reference architecture, real-time PAM processing, Navy-invested ($3M)
- **priority**: P1

### 13.2 SpeechBrain
- **id**: `speechbrain`
- **url**: https://speechbrain.readthedocs.io/
- **type**: PyTorch audio/speech ML toolkit (BEATs integration, classification, separation)
- **access**: open source
- **nlm_target**: NLM training framework, BEATs fine-tuning
- **priority**: P0

```bash
pip install speechbrain
```

### 13.3 Librosa
- **id**: `librosa`
- **url**: https://librosa.org/
- **type**: Python audio analysis (MFCCs, spectrograms, chromagrams)
- **access**: open source
- **nlm_target**: feature extraction pipeline
- **priority**: P0

```bash
pip install librosa
```

---

## SECTION 14: IMPLEMENTATION PRIORITY MATRIX

### Tier P0 — Immediate (Week 1)
These are required dependencies. Download and integrate first.

| ID | Name | NLM Subsystem | Action |
|---|---|---|---|
| `panns` | PANNs CNN14 | Foundation backbone | Clone repo, download weights, integrate as feature extractor |
| `beats` | BEATs | Foundation backbone (alt) | Install SpeechBrain, load checkpoint |
| `panns_deepship` | PANNs+DeepShip | Vessel classifier | Clone, use as starting UATR model |
| `uwtrl_meg` | UWTRL-MEG | Vessel + localization | Download from HuggingFace |
| `ds3500` | DS3500 | Vessel training data | `load_dataset("peng7554/DS3500")` |
| `shipsear` | ShipsEar | Vessel training data | Request via publication |
| `deepship` | DeepShip | Vessel training data | Request via publication |
| `watkins_whoi` | Watkins/WHOI | Marine mammal training | Clone GitHub helper, download |
| `esc50` | ESC-50 | Environmental baseline | `load_dataset("ashraq/esc50")` |
| `audioset` | Google AudioSet | Pre-training corpus | Download embeddings + ontology |
| `mbari_pacific_sound` | MBARI Pacific Sound | Ambient ocean baseline | Connect to AWS S3 bucket |
| `sanctsound` | SanctSound | Multi-modal training | Download from NCEI |
| `noaa_nrs` | NOAA NRS | Ambient baseline | Download from NCEI portal |
| `woa_soundspeed` | WOA Sound Speed | Propagation modeling | Download NetCDF from NCEI |
| `gebco_2025` | GEBCO 2025 | Bathymetry | Download from download.gebco.net |
| `wmm2025` | WMM2025 | MAD baseline | Download model coefficients |
| `emag2v3` | EMAG2v3 | MAD crustal anomaly | Download GeoTIFF from NCEI |
| `noaa_ais` | NOAA AccessAIS | Ground truth | Download from Digital Coast |
| `ndbc` | NDBC | Environmental context | API access |
| `speechbrain` | SpeechBrain | Training framework | `pip install speechbrain` |
| `librosa` | Librosa | Feature extraction | `pip install librosa` |

### Tier P1 — Week 1-2

| ID | Name | NLM Subsystem | Action |
|---|---|---|---|
| `xenocanto` | Xeno-canto | Bird/drone discrimination | HuggingFace or API |
| `droneaudioset` | DroneAudioset | Drone detection | Request via publication |
| `dclde` | DCLDE | Marine mammal benchmark | Request workshop data |
| `noaa_fisheries_mma` | NOAA Fisheries | Marine mammal ops | Download from NOAA |
| `macaulay` | Macaulay Library | Broad species coverage | API/web access |
| `uxo_zenodo` | UXO Dataset | Threat detection | Download from Zenodo |
| `glacier_bay` | Glacier Bay | Labelled multi-class | Download from NPS |
| `mag_intrusion` | Mag Intrusion Net | MAD reference arch | Read paper |
| `ncei_pad` | NCEI PAD Archive | Comprehensive PAM | NCEI portal |
| `global_acoustic_params` | Global Acoustic Params | Propagation lookup | Download supplement |
| `navoceano_moods` | NAVOCEANO MOODS | Navy env data | Data.gov + Zeetachec request |
| `copernicus_marine` | Copernicus Marine | Ocean state | pip install copernicusmarine |
| `hearmyship` | HearMyShip | Small vessel ID | Download via publication |
| `qiandaoear22` | QiandaoEar22 | Vessel-specific ID | Request via publication |
| `ast` | AST | Backbone alternative | Clone GitHub |
| `urbansound8k` | UrbanSound8K | Transfer learning | HuggingFace |
| `fsd50k` | FSD50K | Audio event classification | Zenodo download |
| `uatd` | UATD | Sonar target detection | Figshare download |
| `roboflow_sonar` | Roboflow Sonar | Sonar detection | Roboflow API |
| `opensonar` | OpenSonarDatasets | Sonar directory | GitHub |
| `ushant_ais` | Ushant AIS | Trajectory modeling | GitHub clone |
| `marinecadastre` | MarineCadastre | Bulk historical AIS | Download |
| `pamguard` | PAMGuard | PAM reference | Download software |

### Tier P2 — Week 2-4

| ID | Name |
|---|---|
| `wolfset` | Wolfset UATR benchmark |
| `kaggle_uasmr` | Kaggle underwater signal modulation |
| `dtic_marine_sounds` | DTIC marine animal sounds |
| `fishsounds` | FishSounds.net |
| `uav_32cat` | 32-Category UAV sounds |
| `iogp_explosions` | IOGP underwater explosions |
| `shallow_explosions` | Shallow explosion recordings |
| `freesound` | Freesound.org |
| `dcase` | DCASE challenge datasets |
| `noaa_seasounds` | NOAA Sea Sounds |
| `nasa_earthdata` | NASA Earthdata ocean |
| `datagov_magnetic` | Data.gov magnetic datasets |
| `fish_classifier` | Fish sound classifier |
| `underwater_snd` | Underwater snd framework |
| `frcnn_marine` | Faster R-CNN marine mammal |
| `sctd` | SCTD sonar detection |
| `iqoe_portal` | IQOE portal |
| `uk_acoustics_directory` | UK Acoustics directory |
| `ioos` | IOOS regional observing |
| `onc` | Ocean Networks Canada |
| `global_maritime_traffic` | Global maritime traffic |

### Tier P3 — Backlog

| ID | Name |
|---|---|
| `emso` | EMSO-ERIC observatories |
| `galway_bay` | Galway Bay hydrophone |
| `mobysound` | MobySound/CIMRS |
| `ibcao` | IBCAO Arctic bathymetry |
| `ibcso` | IBCSO Southern Ocean |
| `maid` | MAID magnetic interpolation |
| `bbc_sfx` | BBC Sound Effects |
| `navfac_aircraft` | NAVFAC aircraft (restricted) |
| `serdp_aircraft` | SERDP aircraft (DoD) |

---

## SECTION 15: DATA PIPELINE ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────┐
│                    NLM DATA PIPELINE                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  RAW SOURCES              ETL LAYER              TRAINING       │
│  ───────────              ─────────              ────────       │
│                                                                 │
│  AWS S3 (MBARI) ──┐                                            │
│  NCEI (NRS/SS) ───┤   ┌──────────────┐   ┌──────────────┐     │
│  GEBCO NetCDF ────┤──▶│  mindex ETL  │──▶│  NLM Train   │     │
│  WMM2025 ─────────┤   │  pipelines   │   │  Dataloaders │     │
│  EMAG2v3 ─────────┤   │              │   │              │     │
│  NOAA AIS ────────┤   │  - resample  │   │  - PANNs     │     │
│  NDBC ────────────┤   │  - normalize │   │  - BEATs     │     │
│  Copernicus ──────┤   │  - label     │   │  - UWTRL     │     │
│                   │   │  - augment   │   │  - Custom    │     │
│  HuggingFace ─────┤   │  - validate  │   │              │     │
│  (DS3500, ESC50,  │   │  - vectorize │   └──────┬───────┘     │
│   Xeno-canto)     │   └──────────────┘          │              │
│                   │           │                   │              │
│  GitHub repos ────┘           ▼                   ▼              │
│  (ShipsEar,            ┌───────────┐      ┌───────────┐        │
│   WHOI, etc.)          │  Mindex   │      │ NLM Model │        │
│                        │  Vector   │      │ Weights   │        │
│                        │  Store    │      │ Registry  │        │
│                        └───────────┘      └─────┬─────┘        │
│                              │                   │              │
│                              ▼                   ▼              │
│                        ┌─────────────────────────────┐         │
│                        │     FUSARIUM / CREP          │         │
│                        │     Real-time Inference       │         │
│                        │     (mycosoft-mas + website)  │         │
│                        └─────────────────────────────┘         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## SECTION 16: QUICK-START COMMANDS

```bash
# === STEP 1: Clone model repos ===
git clone https://github.com/qiuqiangkong/audioset_tagging_cnn.git  # PANNs
git clone https://github.com/microsoft/unilm.git                     # BEATs
git clone https://github.com/YuanGongND/ast.git                      # AST
git clone https://github.com/doans/Underwater-Acoustic-Target-Classification-Based-on-Dense-Convolutional-Neural-Network.git  # PANNs+DeepShip
git clone https://github.com/lucascesarfd/underwater_snd.git          # Underwater snd
git clone https://github.com/mopg/getWHOIdata.git                    # Watkins/WHOI
git clone https://github.com/remaro-network/OpenSonarDatasets.git     # Sonar directory
git clone https://github.com/rtavenar/ushant_ais.git                  # Ushant AIS
git clone https://github.com/karolpiczak/ESC-50.git                   # ESC-50

# === STEP 2: Download pre-trained weights ===
mkdir -p weights/
wget -P weights/ https://zenodo.org/records/3987831/files/Cnn14_mAP%3D0.431.pth  # PANNs CNN14

# === STEP 3: Download environmental data ===
mkdir -p data/environment/
# GEBCO 2025 — download from https://download.gebco.net (requires form)
# EMAG2v3
wget -P data/environment/ https://www.ncei.noaa.gov/data/earth-magnetic-model-anomaly-grid/access/EMAG2v3/EMAG2_V3_20170530.tif

# === STEP 4: Download HuggingFace datasets ===
python3 -c "
from datasets import load_dataset
load_dataset('peng7554/DS3500', cache_dir='data/vessel/')
load_dataset('ashraq/esc50', cache_dir='data/environmental/')
# load_dataset('ilyassmoummad/Xeno-Canto-6s-16khz', cache_dir='data/aerial/')  # Large ~50GB
"

# === STEP 5: Install training stack ===
pip install torch torchaudio librosa soundfile scipy numpy pandas
pip install datasets transformers huggingface_hub speechbrain
pip install netCDF4 xarray h5py rasterio
pip install copernicusmarine
```

---

## TOTAL: 70+ SOURCES ACROSS 14 CATEGORIES

All open-access sources can be integrated immediately.
Restricted sources (NAVFAC, NAVOCEANO classified subsets) require clearance via Zeetachec's Navy contracting vehicle.

This document is additive to `TACO_CURSOR_IMPLEMENTATION_PLAN.md` — use both together in Cursor.
