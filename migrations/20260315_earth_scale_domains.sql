-- ============================================================================
-- EARTH-SCALE DATA DOMAINS MIGRATION
-- March 15, 2026
--
-- Expands MINDEX from fungi-centric to full planetary awareness:
--   earth.*    – Natural events (earthquakes, volcanoes, storms, wildfires, etc.)
--   species.*  – All living organisms (plants, birds, mammals, insects, marine, etc.)
--   infra.*    – Human infrastructure (factories, power, mining, dams, antennas, etc.)
--   signals.*  – RF/signal landscape (cell, wifi, bluetooth, AM/FM, internet cables)
--   transport.*– Aviation, maritime, shipping, spaceports, vehicle tracking
--   space.*    – Space weather, satellites, solar events, NASA/NOAA feeds
--   monitor.*  – Webcams, CCTV, public surveillance feeds
--   atmos.*    – Atmosphere (CO2, methane, air quality, MODIS, Landsat, AIRS)
--   hydro.*    – Water systems (buoys, rivers, treatment plants, ocean data)
-- ============================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- EARTH SCHEMA – Natural Events & Hazards
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS earth;

CREATE TABLE IF NOT EXISTS earth.earthquakes (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- usgs, emsc, etc.
    source_id       VARCHAR(100) UNIQUE,
    magnitude       DOUBLE PRECISION NOT NULL,
    magnitude_type  VARCHAR(10),           -- ml, mb, mw, ms
    depth_km        DOUBLE PRECISION,
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    place_name      TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL,
    tsunami_flag    BOOLEAN DEFAULT FALSE,
    felt_reports    INTEGER DEFAULT 0,
    alert_level     VARCHAR(20),           -- green, yellow, orange, red
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eq_time ON earth.earthquakes (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_eq_mag ON earth.earthquakes (magnitude DESC);
CREATE INDEX IF NOT EXISTS idx_eq_geo ON earth.earthquakes USING GIST (location);

CREATE TABLE IF NOT EXISTS earth.volcanoes (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- smithsonian, usgs
    source_id       VARCHAR(100) UNIQUE,
    name            TEXT NOT NULL,
    volcano_type    VARCHAR(50),
    elevation_m     DOUBLE PRECISION,
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    country         VARCHAR(100),
    region          VARCHAR(200),
    last_eruption   TEXT,
    alert_level     VARCHAR(20),
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_volc_geo ON earth.volcanoes USING GIST (location);

CREATE TABLE IF NOT EXISTS earth.wildfires (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- firms, nifc, calfire
    source_id       VARCHAR(100),
    name            TEXT,
    location        GEOGRAPHY(GEOMETRY, 4326) NOT NULL,  -- point or polygon
    area_acres      DOUBLE PRECISION,
    containment_pct DOUBLE PRECISION,
    status          VARCHAR(50),           -- active, contained, out
    detected_at     TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ,
    brightness      DOUBLE PRECISION,      -- FIRMS brightness
    frp             DOUBLE PRECISION,      -- fire radiative power
    confidence      VARCHAR(10),
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fire_time ON earth.wildfires (detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_fire_geo ON earth.wildfires USING GIST (location);

CREATE TABLE IF NOT EXISTS earth.storms (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- nhc, jtwc, noaa
    source_id       VARCHAR(100),
    name            TEXT,
    storm_type      VARCHAR(50) NOT NULL,  -- hurricane, typhoon, tropical_storm, cyclone
    category        INTEGER,
    wind_speed_kts  DOUBLE PRECISION,
    pressure_mb     DOUBLE PRECISION,
    track           GEOGRAPHY(LINESTRING, 4326),
    current_location GEOGRAPHY(POINT, 4326),
    status          VARCHAR(50),
    observed_at     TIMESTAMPTZ NOT NULL,
    forecast        JSONB,                 -- forecast cone, track predictions
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_storm_time ON earth.storms (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_storm_geo ON earth.storms USING GIST (current_location);

CREATE TABLE IF NOT EXISTS earth.lightning (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- blitzortung, vaisala, entln
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    polarity        VARCHAR(10),           -- positive, negative
    peak_current_ka DOUBLE PRECISION,
    stroke_type     VARCHAR(20),           -- cloud_to_ground, intra_cloud
    occurred_at     TIMESTAMPTZ NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lightning_time ON earth.lightning (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_lightning_geo ON earth.lightning USING GIST (location);

CREATE TABLE IF NOT EXISTS earth.tornadoes (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,
    source_id       VARCHAR(100),
    ef_rating       INTEGER,               -- EF0-EF5
    path_start      GEOGRAPHY(POINT, 4326),
    path_end        GEOGRAPHY(POINT, 4326),
    path_length_mi  DOUBLE PRECISION,
    path_width_yd   DOUBLE PRECISION,
    fatalities      INTEGER DEFAULT 0,
    injuries        INTEGER DEFAULT 0,
    occurred_at     TIMESTAMPTZ NOT NULL,
    state           VARCHAR(50),
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tornado_time ON earth.tornadoes (occurred_at DESC);

CREATE TABLE IF NOT EXISTS earth.floods (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- nws, gfms, dartmouth
    source_id       VARCHAR(100),
    flood_type      VARCHAR(50),           -- flash, riverine, coastal, urban
    severity        VARCHAR(20),
    area            GEOGRAPHY(GEOMETRY, 4326),
    observed_at     TIMESTAMPTZ NOT NULL,
    water_level_m   DOUBLE PRECISION,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_flood_geo ON earth.floods USING GIST (area);

-- ============================================================================
-- SPECIES SCHEMA – All Living Organisms (extends existing core.taxon)
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS species;

-- Universal species catalog (supplements core.taxon with non-fungal kingdoms)
CREATE TABLE IF NOT EXISTS species.organisms (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- gbif, inat, ebird, fishbase, etc.
    source_id       VARCHAR(100),
    kingdom         VARCHAR(50) NOT NULL,  -- Fungi, Plantae, Animalia, Bacteria, etc.
    phylum          VARCHAR(100),
    class_name      VARCHAR(100),
    order_name      VARCHAR(100),
    family          VARCHAR(100),
    genus           VARCHAR(100),
    species_name    VARCHAR(200),
    scientific_name VARCHAR(300) NOT NULL,
    common_name     TEXT,
    rank            VARCHAR(50),
    conservation_status VARCHAR(20),       -- LC, NT, VU, EN, CR, EW, EX (IUCN)
    habitat         TEXT,
    description     TEXT,
    image_url       TEXT,
    taxonomy_id     INTEGER,               -- GBIF/iNat taxon key
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_species_kingdom ON species.organisms (kingdom);
CREATE INDEX IF NOT EXISTS idx_species_sciname ON species.organisms (scientific_name);
CREATE INDEX IF NOT EXISTS idx_species_common ON species.organisms USING gin (to_tsvector('english', COALESCE(common_name, '')));

-- Species observations / sightings (all kingdoms)
CREATE TABLE IF NOT EXISTS species.sightings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organism_id     UUID REFERENCES species.organisms(id),
    source          VARCHAR(50) NOT NULL,
    source_id       VARCHAR(100),
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    observed_at     TIMESTAMPTZ,
    observer        TEXT,
    image_url       TEXT,
    quality_grade   VARCHAR(20),
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sight_geo ON species.sightings USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_sight_time ON species.sightings (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_sight_organism ON species.sightings (organism_id);

-- ============================================================================
-- INFRA SCHEMA – Human Infrastructure & Pollution Sources
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS infra;

CREATE TABLE IF NOT EXISTS infra.facilities (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- epa, eia, osm, frs
    source_id       VARCHAR(100),
    name            TEXT NOT NULL,
    facility_type   VARCHAR(100) NOT NULL, -- factory, power_plant, mining, oil_gas, refinery, dam, water_treatment, waste
    sub_type        VARCHAR(100),          -- coal, nuclear, solar, wind, hydro, etc.
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    address         TEXT,
    city            VARCHAR(200),
    state_province  VARCHAR(200),
    country         VARCHAR(100),
    operator        TEXT,
    capacity        TEXT,
    status          VARCHAR(50),           -- active, closed, planned
    emissions       JSONB,                 -- CO2, NOx, SOx, PM2.5, etc.
    permits         JSONB,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_fac_type ON infra.facilities (facility_type);
CREATE INDEX IF NOT EXISTS idx_fac_geo ON infra.facilities USING GIST (location);

CREATE TABLE IF NOT EXISTS infra.power_grid (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,
    source_id       VARCHAR(100),
    asset_type      VARCHAR(50) NOT NULL,  -- substation, transmission_line, transformer
    name            TEXT,
    voltage_kv      DOUBLE PRECISION,
    location        GEOGRAPHY(GEOMETRY, 4326) NOT NULL,  -- point or line
    operator        TEXT,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_grid_geo ON infra.power_grid USING GIST (location);

CREATE TABLE IF NOT EXISTS infra.water_systems (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,
    source_id       VARCHAR(100),
    system_type     VARCHAR(50) NOT NULL,  -- river, dam, reservoir, treatment_plant, pipeline, aqueduct
    name            TEXT NOT NULL,
    location        GEOGRAPHY(GEOMETRY, 4326) NOT NULL,
    capacity        TEXT,
    water_quality   JSONB,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_water_geo ON infra.water_systems USING GIST (location);

CREATE TABLE IF NOT EXISTS infra.internet_cables (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL DEFAULT 'submarinecablemap',
    source_id       VARCHAR(100),
    name            TEXT NOT NULL,
    cable_type      VARCHAR(50),           -- submarine, terrestrial
    length_km       DOUBLE PRECISION,
    capacity_tbps   DOUBLE PRECISION,
    route           GEOGRAPHY(LINESTRING, 4326),
    landing_points  JSONB,                 -- array of {name, lat, lng, country}
    owners          JSONB,
    rfs_date        DATE,                  -- ready for service
    status          VARCHAR(50),
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cable_route ON infra.internet_cables USING GIST (route);

-- ============================================================================
-- SIGNALS SCHEMA – RF, Antennas, Wireless Infrastructure
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS signals;

CREATE TABLE IF NOT EXISTS signals.antennas (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- fcc, opencellid, wigle
    source_id       VARCHAR(100),
    antenna_type    VARCHAR(50) NOT NULL,  -- cell_tower, am_radio, fm_radio, tv_broadcast, microwave, wifi_ap
    frequency_mhz   DOUBLE PRECISION,
    band            VARCHAR(50),           -- 700MHz, 850MHz, 1900MHz, 2.4GHz, 5GHz
    operator        TEXT,
    call_sign       VARCHAR(50),
    power_watts     DOUBLE PRECISION,
    height_m        DOUBLE PRECISION,
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    technology      VARCHAR(50),           -- 4G_LTE, 5G_NR, 3G, GSM, CDMA
    status          VARCHAR(50),
    coverage_radius_m DOUBLE PRECISION,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_ant_type ON signals.antennas (antenna_type);
CREATE INDEX IF NOT EXISTS idx_ant_geo ON signals.antennas USING GIST (location);

CREATE TABLE IF NOT EXISTS signals.wifi_hotspots (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- wigle, osm, municipal
    ssid            TEXT,
    bssid           VARCHAR(20),
    encryption      VARCHAR(20),
    channel         INTEGER,
    frequency_mhz   INTEGER,
    signal_dbm      INTEGER,
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    last_seen       TIMESTAMPTZ,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wifi_geo ON signals.wifi_hotspots USING GIST (location);

CREATE TABLE IF NOT EXISTS signals.signal_measurements (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    measurement_type VARCHAR(50) NOT NULL, -- rf_power, emf, wifi_rssi, cell_rssi
    frequency_mhz   DOUBLE PRECISION,
    value           DOUBLE PRECISION NOT NULL,
    unit            VARCHAR(20) NOT NULL,  -- dBm, V/m, mW
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    measured_at     TIMESTAMPTZ NOT NULL,
    device_id       UUID,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sig_geo ON signals.signal_measurements USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_sig_time ON signals.signal_measurements (measured_at DESC);

-- ============================================================================
-- TRANSPORT SCHEMA – Aviation, Maritime, Shipping, Spaceports
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS transport;

CREATE TABLE IF NOT EXISTS transport.aircraft (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- adsb_exchange, opensky, flightradar24
    icao24          VARCHAR(6),
    callsign        VARCHAR(20),
    registration    VARCHAR(20),
    aircraft_type   VARCHAR(50),
    origin          VARCHAR(10),           -- ICAO airport code
    destination     VARCHAR(10),
    location        GEOGRAPHY(POINT, 4326),
    altitude_ft     DOUBLE PRECISION,
    ground_speed_kts DOUBLE PRECISION,
    heading         DOUBLE PRECISION,
    vertical_rate   DOUBLE PRECISION,
    on_ground       BOOLEAN DEFAULT FALSE,
    squawk          VARCHAR(4),
    observed_at     TIMESTAMPTZ NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aircraft_time ON transport.aircraft (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_aircraft_geo ON transport.aircraft USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_aircraft_icao ON transport.aircraft (icao24);

CREATE TABLE IF NOT EXISTS transport.vessels (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- ais, marinetraffic, vesselfinder
    mmsi            VARCHAR(9),
    imo             VARCHAR(10),
    name            TEXT,
    vessel_type     VARCHAR(100),
    flag            VARCHAR(50),
    location        GEOGRAPHY(POINT, 4326),
    speed_knots     DOUBLE PRECISION,
    course          DOUBLE PRECISION,
    heading         DOUBLE PRECISION,
    destination     TEXT,
    draught_m       DOUBLE PRECISION,
    nav_status      VARCHAR(50),
    observed_at     TIMESTAMPTZ NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vessel_time ON transport.vessels (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_vessel_geo ON transport.vessels USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_vessel_mmsi ON transport.vessels (mmsi);

CREATE TABLE IF NOT EXISTS transport.airports (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL DEFAULT 'ourairports',
    icao_code       VARCHAR(4) UNIQUE,
    iata_code       VARCHAR(3),
    name            TEXT NOT NULL,
    airport_type    VARCHAR(50),           -- large, medium, small, heliport, seaplane, closed
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    elevation_ft    DOUBLE PRECISION,
    country         VARCHAR(100),
    region          VARCHAR(200),
    municipality    TEXT,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_airport_geo ON transport.airports USING GIST (location);

CREATE TABLE IF NOT EXISTS transport.ports (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,
    source_id       VARCHAR(100),
    name            TEXT NOT NULL,
    port_type       VARCHAR(50),           -- seaport, river_port, inland_port
    unlocode        VARCHAR(10),
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    country         VARCHAR(100),
    max_vessel_size TEXT,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_port_geo ON transport.ports USING GIST (location);

CREATE TABLE IF NOT EXISTS transport.spaceports (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL DEFAULT 'manual',
    name            TEXT NOT NULL,
    operator        TEXT,
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    country         VARCHAR(100),
    orbital_capable BOOLEAN DEFAULT FALSE,
    status          VARCHAR(50),
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_spaceport_geo ON transport.spaceports USING GIST (location);

CREATE TABLE IF NOT EXISTS transport.launches (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- launch_library, spacex_api
    source_id       VARCHAR(100),
    name            TEXT NOT NULL,
    provider        TEXT,
    vehicle         TEXT,
    mission_type    VARCHAR(100),
    pad_name        TEXT,
    pad_location    GEOGRAPHY(POINT, 4326),
    launch_time     TIMESTAMPTZ,
    status          VARCHAR(50),           -- upcoming, success, failure, partial_failure
    orbit           VARCHAR(50),           -- LEO, GEO, MEO, SSO, etc.
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_launch_time ON transport.launches (launch_time DESC);

-- ============================================================================
-- SPACE SCHEMA – Satellites, Space Weather, Solar Events
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS space;

CREATE TABLE IF NOT EXISTS space.satellites (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- celestrak, space-track, n2yo
    norad_id        INTEGER UNIQUE,
    cospar_id       VARCHAR(20),
    name            TEXT NOT NULL,
    satellite_type  VARCHAR(100),          -- weather, gps, comm, earth_obs, military, debris
    operator        TEXT,
    launch_date     DATE,
    orbit_type      VARCHAR(50),           -- LEO, MEO, GEO, HEO, SSO
    perigee_km      DOUBLE PRECISION,
    apogee_km       DOUBLE PRECISION,
    inclination_deg DOUBLE PRECISION,
    period_min      DOUBLE PRECISION,
    tle_line1       TEXT,
    tle_line2       TEXT,
    tle_epoch       TIMESTAMPTZ,
    status          VARCHAR(50),           -- active, inactive, decayed, debris
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sat_type ON space.satellites (satellite_type);
CREATE INDEX IF NOT EXISTS idx_sat_norad ON space.satellites (norad_id);

CREATE TABLE IF NOT EXISTS space.solar_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- noaa_swpc, soho, stereo
    event_type      VARCHAR(50) NOT NULL,  -- solar_flare, cme, geomagnetic_storm, solar_wind
    class           VARCHAR(10),           -- A, B, C, M, X (flare class)
    intensity       DOUBLE PRECISION,
    kp_index        DOUBLE PRECISION,      -- geomagnetic
    speed_km_s      DOUBLE PRECISION,      -- CME/solar wind speed
    source_region   VARCHAR(50),           -- active region number
    start_time      TIMESTAMPTZ NOT NULL,
    peak_time       TIMESTAMPTZ,
    end_time        TIMESTAMPTZ,
    earth_directed  BOOLEAN,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_solar_time ON space.solar_events (start_time DESC);
CREATE INDEX IF NOT EXISTS idx_solar_type ON space.solar_events (event_type);

-- ============================================================================
-- ATMOS SCHEMA – Atmosphere, Air Quality, Remote Sensing
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS atmos;

CREATE TABLE IF NOT EXISTS atmos.air_quality (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- openaq, airnow, epa, purpleair
    source_id       VARCHAR(100),
    station_name    TEXT,
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    parameter       VARCHAR(20) NOT NULL,  -- pm25, pm10, o3, no2, so2, co, aqi
    value           DOUBLE PRECISION NOT NULL,
    unit            VARCHAR(20) NOT NULL,  -- ug/m3, ppb, ppm, aqi_index
    measured_at     TIMESTAMPTZ NOT NULL,
    averaging_period VARCHAR(20),          -- 1h, 8h, 24h
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aq_geo ON atmos.air_quality USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_aq_time ON atmos.air_quality (measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_aq_param ON atmos.air_quality (parameter);

CREATE TABLE IF NOT EXISTS atmos.greenhouse_gas (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- noaa_gml, copernicus, oco2
    source_id       VARCHAR(100),
    gas_type        VARCHAR(20) NOT NULL,  -- co2, ch4, n2o, sf6
    value           DOUBLE PRECISION NOT NULL,
    unit            VARCHAR(20) NOT NULL,  -- ppm, ppb
    location        GEOGRAPHY(POINT, 4326),
    measured_at     TIMESTAMPTZ NOT NULL,
    station_name    TEXT,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ghg_gas ON atmos.greenhouse_gas (gas_type);
CREATE INDEX IF NOT EXISTS idx_ghg_time ON atmos.greenhouse_gas (measured_at DESC);

CREATE TABLE IF NOT EXISTS atmos.weather_observations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- noaa_isd, openweathermap, wmo
    station_id      VARCHAR(50),
    station_name    TEXT,
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    temperature_c   DOUBLE PRECISION,
    humidity_pct    DOUBLE PRECISION,
    pressure_hpa    DOUBLE PRECISION,
    wind_speed_ms   DOUBLE PRECISION,
    wind_direction  DOUBLE PRECISION,
    precipitation_mm DOUBLE PRECISION,
    visibility_m    DOUBLE PRECISION,
    cloud_cover_pct DOUBLE PRECISION,
    conditions      TEXT,
    observed_at     TIMESTAMPTZ NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wx_geo ON atmos.weather_observations USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_wx_time ON atmos.weather_observations (observed_at DESC);

CREATE TABLE IF NOT EXISTS atmos.remote_sensing (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- modis, landsat, sentinel, airs, viirs
    product         VARCHAR(100) NOT NULL, -- ndvi, lst, aod, true_color, fire_hotspots
    satellite       VARCHAR(50),
    tile_id         VARCHAR(50),
    bbox            GEOGRAPHY(POLYGON, 4326),
    centroid        GEOGRAPHY(POINT, 4326),
    resolution_m    DOUBLE PRECISION,
    acquisition_time TIMESTAMPTZ NOT NULL,
    cloud_cover_pct DOUBLE PRECISION,
    data_url        TEXT,
    thumbnail_url   TEXT,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rs_geo ON atmos.remote_sensing USING GIST (bbox);
CREATE INDEX IF NOT EXISTS idx_rs_time ON atmos.remote_sensing (acquisition_time DESC);
CREATE INDEX IF NOT EXISTS idx_rs_source ON atmos.remote_sensing (source, product);

-- ============================================================================
-- HYDRO SCHEMA – Buoys, Rivers, Oceans, Water Quality
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS hydro;

CREATE TABLE IF NOT EXISTS hydro.buoys (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- ndbc, sofar, ioos
    station_id      VARCHAR(20) NOT NULL,
    name            TEXT,
    buoy_type       VARCHAR(50),           -- weather, wave, current, tsunami, ice
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    water_temp_c    DOUBLE PRECISION,
    wave_height_m   DOUBLE PRECISION,
    wave_period_s   DOUBLE PRECISION,
    wind_speed_ms   DOUBLE PRECISION,
    wind_direction  DOUBLE PRECISION,
    pressure_hpa    DOUBLE PRECISION,
    air_temp_c      DOUBLE PRECISION,
    observed_at     TIMESTAMPTZ NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_buoy_geo ON hydro.buoys USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_buoy_time ON hydro.buoys (observed_at DESC);

CREATE TABLE IF NOT EXISTS hydro.stream_gauges (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL DEFAULT 'usgs_nwis',
    site_id         VARCHAR(20) NOT NULL,
    name            TEXT,
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    discharge_cfs   DOUBLE PRECISION,
    gauge_height_ft DOUBLE PRECISION,
    water_temp_c    DOUBLE PRECISION,
    observed_at     TIMESTAMPTZ NOT NULL,
    flood_stage_ft  DOUBLE PRECISION,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gauge_geo ON hydro.stream_gauges USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_gauge_time ON hydro.stream_gauges (observed_at DESC);

-- ============================================================================
-- MONITOR SCHEMA – Webcams, CCTV, Public Feeds
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS monitor;

CREATE TABLE IF NOT EXISTS monitor.cameras (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL,  -- windy, dot, insecam, webcamtaxi
    source_id       VARCHAR(100),
    name            TEXT,
    camera_type     VARCHAR(50),           -- webcam, traffic, weather, wildlife, volcano
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    stream_url      TEXT,
    snapshot_url    TEXT,
    city            VARCHAR(200),
    country         VARCHAR(100),
    status          VARCHAR(20),           -- online, offline
    last_checked    TIMESTAMPTZ,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cam_geo ON monitor.cameras USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_cam_type ON monitor.cameras (camera_type);

-- ============================================================================
-- MILITARY SCHEMA – Publicly Known Military Installations
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS military;

CREATE TABLE IF NOT EXISTS military.installations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          VARCHAR(50) NOT NULL DEFAULT 'osm',
    source_id       VARCHAR(100),
    name            TEXT NOT NULL,
    installation_type VARCHAR(100),        -- base, airfield, naval_station, missile_site, radar
    branch          VARCHAR(50),           -- army, navy, air_force, marines, coast_guard, space_force
    country         VARCHAR(100),
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    status          VARCHAR(50),
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mil_geo ON military.installations USING GIST (location);

-- ============================================================================
-- Add full-text search indexes on key text columns across all schemas
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_species_fts ON species.organisms
    USING gin (to_tsvector('english', scientific_name || ' ' || COALESCE(common_name, '')));
CREATE INDEX IF NOT EXISTS idx_fac_fts ON infra.facilities
    USING gin (to_tsvector('english', name || ' ' || facility_type || ' ' || COALESCE(sub_type, '')));
CREATE INDEX IF NOT EXISTS idx_ant_fts ON signals.antennas
    USING gin (to_tsvector('english', antenna_type || ' ' || COALESCE(operator, '') || ' ' || COALESCE(technology, '')));
