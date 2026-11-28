"use client";

import React from "react";
import type { StationSummary } from "../lib/api";

interface UkStationMapProps {
  stations: StationSummary[];
  selectedStationId: number | null;
  onSelectStation: (stationId: number) => void;
}

// Equirectangular projection aspect-corrected for 52.5°N (cos ≈ 0.609)
function projectUk(lat: number, lon: number): { x: number; y: number } {
  const x = 22 + ((lon - -6.0) / 8.2) * 268;
  const y = 18 + ((55.85 - lat) / 6.0) * 324;
  return { x, y };
}

// Enlarged projection for Greater London Metro inset (lon -0.58..0.32, lat 51.36..51.62)
function projectLondonInset(lat: number, lon: number): { x: number; y: number } {
  const x = 304 + ((lon - -0.58) / 0.9) * 124;
  const y = 38 + ((51.62 - lat) / 0.26) * 134;
  return { x, y };
}

const ENGLAND_WALES_COAST_LON_LAT: Array<[number, number]> = [
  [-2.0, 55.77], // Berwick-upon-Tweed
  [-1.56, 55.34], // Amble
  [-1.42, 55.02], // Tynemouth
  [-1.36, 54.91], // Sunderland
  [-1.14, 54.62], // Teesmouth
  [-0.61, 54.49], // Whitby
  [-0.4, 54.28], // Scarborough
  [-0.08, 54.11], // Flamborough Head
  [0.11, 53.58], // Spurn Head
  [-0.34, 53.74], // Hull / Humber inner
  [0.05, 53.52], // Grimsby
  [0.34, 53.15], // Skegness
  [0.15, 52.82], // Boston / The Wash
  [0.49, 52.94], // Hunstanton
  [1.3, 52.93], // Cromer
  [1.74, 52.61], // Great Yarmouth
  [1.76, 52.48], // Lowestoft
  [1.58, 52.1], // Aldeburgh
  [1.35, 51.95], // Felixstowe
  [1.07, 51.79], // St Osyth / Clacton
  [0.92, 51.62], // Dengie
  [0.72, 51.53], // Southend
  [0.45, 51.49], // Thames north shore
  [0.75, 51.44], // Sheppey
  [1.44, 51.38], // Margate
  [1.32, 51.13], // Dover
  [0.98, 50.91], // Dungeness
  [0.29, 50.77], // Eastbourne
  [-0.14, 50.82], // Brighton
  [-1.09, 50.79], // Portsmouth
  [-1.32, 50.86], // Southampton Water
  [-1.55, 50.74], // Lymington
  [-1.88, 50.72], // Bournemouth
  [-2.45, 50.52], // Portland Bill
  [-3.48, 50.62], // Exmouth
  [-3.53, 50.46], // Torquay
  [-4.14, 50.37], // Plymouth
  [-5.2, 49.96], // Lizard Point
  [-5.71, 50.07], // Land's End
  [-5.48, 50.21], // St Ives
  [-5.05, 50.42], // Newquay
  [-4.55, 50.83], // Bude
  [-4.22, 51.05], // Barnstaple Bay
  [-3.48, 51.21], // Minehead
  [-2.99, 51.34], // Weston-super-Mare
  [-2.65, 51.48], // Avonmouth
  [-3.17, 51.47], // Cardiff
  [-3.94, 51.62], // Swansea Bay
  [-4.3, 51.68], // Llanelli
  [-5.29, 51.88], // St Davids
  [-4.65, 52.09], // Cardigan
  [-4.09, 52.41], // Aberystwyth
  [-4.12, 52.89], // Porthmadog
  [-4.79, 52.79], // Llyn Peninsula
  [-4.35, 53.15], // Caernarfon
  [-4.64, 53.31], // Anglesey
  [-3.83, 53.33], // Llandudno
  [-3.09, 53.39], // Wirral / Dee
  [-3.0, 53.44], // Liverpool Bay
  [-3.06, 53.82], // Blackpool
  [-2.87, 54.07], // Morecambe
  [-3.23, 54.11], // Barrow
  [-3.6, 54.55], // Workington
  [-3.35, 54.95], // Solway Firth
  [-2.65, 55.28], // Border Cheviots
  [-2.25, 55.62], // Tweed Valley
];

const ISLE_OF_WIGHT: Array<[number, number]> = [
  [-1.57, 50.67],
  [-1.3, 50.76],
  [-1.08, 50.69],
  [-1.29, 50.58],
];

const THAMES_PATH_LON_LAT: Array<[number, number]> = [
  [-0.95, 51.46],
  [-0.61, 51.48],
  [-0.31, 51.46],
  [-0.12, 51.51],
  [0.18, 51.49],
  [0.52, 51.48],
];

const REFERENCE_CITIES: Array<{ name: string; lat: number; lon: number }> = [
  { name: "Bristol", lat: 51.45, lon: -2.59 },
  { name: "Birmingham", lat: 52.48, lon: -1.89 },
  { name: "Leeds", lat: 53.8, lon: -1.55 },
];

const LONDON_IDS = new Set([141, 153, 154, 157]);

function statusStyle(status: StationSummary["status"]) {
  if (status === "HEALTHY") {
    return {
      fill: "#10b981",
      ring: "#34d399",
      shortLabel: "Healthy",
    };
  }
  if (status === "LATE") {
    return {
      fill: "#f59e0b",
      ring: "#fbbf24",
      shortLabel: "Delayed",
    };
  }
  return {
    fill: "#f43f5e",
    ring: "#fb7185",
    shortLabel: "Gap >12h",
  };
}

export function UkStationMap({
  stations,
  selectedStationId,
  onSelectStation,
}: UkStationMapProps) {
  const ukPolygonPoints = ENGLAND_WALES_COAST_LON_LAT.map(([lon, lat]) => {
    const p = projectUk(lat, lon);
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }).join(" ");

  const iowPoints = ISLE_OF_WIGHT.map(([lon, lat]) => {
    const p = projectUk(lat, lon);
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }).join(" ");

  const thamesPoints = THAMES_PATH_LON_LAT.map(([lon, lat]) => {
    const p = projectUk(lat, lon);
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }).join(" ");

  const londonCenter = projectUk(51.5, -0.15);
  const londonStations = stations.filter((s) =>
    LONDON_IDS.has(s.provider_location_id)
  );

  // Subtle label placement hints for London inset so Harlington & Hillingdon never collide
  const londonLabelOffsets: Record<number, { dx: number; dy: number; short: string }> = {
    153: { dx: 8, dy: -7, short: "Hillingdon" },
    141: { dx: 8, dy: 11, short: "Harlington" },
    154: { dx: -6, dy: -9, short: "Marylebone" },
    157: { dx: -10, dy: 13, short: "Bexley" },
  };

  return (
    <div className="relative w-full rounded-xl border border-slate-800 bg-[#081224] overflow-hidden">
      <svg
        role="img"
        aria-label="Geographic map of Great Britain showing the 8 archived UK AURN air-quality stations and Greater London inset"
        viewBox="0 0 445 360"
        className="w-full h-auto block select-none"
      >
        <defs>
          <radialGradient id="seaGlow" cx="45%" cy="52%" r="65%">
            <stop offset="0%" stopColor="#0f213d" />
            <stop offset="100%" stopColor="#07101f" />
          </radialGradient>
        </defs>

        {/* Sea backdrop */}
        <rect x="0" y="0" width="445" height="360" fill="url(#seaGlow)" />

        {/* Subtle lat/lon graticule */}
        {[51, 52, 53, 54, 55].map((lat) => {
          const y = projectUk(lat, 0).y;
          return (
            <g key={`lat-${lat}`}>
              <line
                x1="14"
                y1={y}
                x2="290"
                y2={y}
                stroke="#1e293b"
                strokeWidth="0.7"
                strokeDasharray="3 4"
              />
              <text x="6" y={y - 3} fill="#475569" fontSize="8" fontFamily="monospace">
                {lat}°N
              </text>
            </g>
          );
        })}
        {[-4, -2, 0].map((lon) => {
          const x = projectUk(50, lon).x;
          return (
            <g key={`lon-${lon}`}>
              <line
                x1={x}
                y1="14"
                x2={x}
                y2="345"
                stroke="#1e293b"
                strokeWidth="0.7"
                strokeDasharray="3 4"
              />
              <text x={x + 2} y="352" fill="#475569" fontSize="8" fontFamily="monospace">
                {lon === 0 ? "0°" : `${Math.abs(lon)}°W`}
              </text>
            </g>
          );
        })}

        {/* England & Wales mainland coastline */}
        <polygon
          points={ukPolygonPoints}
          fill="#182741"
          stroke="#475569"
          strokeWidth="1.35"
          strokeLinejoin="round"
        />
        <polygon
          points={iowPoints}
          fill="#182741"
          stroke="#475569"
          strokeWidth="1.1"
          strokeLinejoin="round"
        />

        {/* River Thames */}
        <polyline
          points={thamesPoints}
          fill="none"
          stroke="#38bdf8"
          strokeOpacity="0.35"
          strokeWidth="1.5"
          strokeLinecap="round"
        />

        {/* Geographic context labels */}
        <text x="125" y="218" fill="#475569" fontSize="8.5" letterSpacing="2">
          WALES
        </text>
        <text x="172" y="168" fill="#475569" fontSize="8.5" letterSpacing="2.5">
          ENGLAND
        </text>
        <text x="165" y="330" fill="#334155" fontSize="8" fontStyle="italic">
          English Channel
        </text>
        <text x="235" y="75" fill="#334155" fontSize="8" fontStyle="italic">
          North Sea
        </text>

        {REFERENCE_CITIES.map((city) => {
          const p = projectUk(city.lat, city.lon);
          return (
            <g key={city.name}>
              <circle cx={p.x} cy={p.y} r="1.8" fill="#64748b" />
              <text x={p.x + 4} y={p.y + 3} fill="#64748b" fontSize="8">
                {city.name}
              </text>
            </g>
          );
        })}

        {/* Greater London Region Highlight & Callout */}
        <rect
          x={londonCenter.x - 16}
          y={londonCenter.y - 9}
          width="32"
          height="18"
          rx="3"
          fill="rgba(99, 102, 241, 0.08)"
          stroke="#6366f1"
          strokeOpacity="0.6"
          strokeWidth="1"
          strokeDasharray="2 2"
        />
        <line
          x1={londonCenter.x + 16}
          y1={londonCenter.y - 4}
          x2="296"
          y2="112"
          stroke="#6366f1"
          strokeOpacity="0.5"
          strokeWidth="1"
          strokeDasharray="3 3"
        />

        {/* Regional + London station markers on Great Britain map */}
        {stations.map((st) => {
          const p = projectUk(st.latitude, st.longitude);
          const isSelected = st.id === selectedStationId;
          const sty = statusStyle(st.status);
          const inLondon = LONDON_IDS.has(st.provider_location_id);

          const labelX =
            st.provider_location_id === 2312
              ? p.x + 7
              : st.provider_location_id === 698
              ? p.x + 7
              : st.provider_location_id === 804
              ? p.x - 4
              : p.x + 7;
          const labelY =
            st.provider_location_id === 804 ? p.y - 7 : p.y + 3;

          return (
            <g
              key={`uk-marker-${st.id}`}
              role="button"
              tabIndex={0}
              aria-label={`Select ${st.name}, status ${st.status}`}
              onClick={() => onSelectStation(st.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelectStation(st.id);
                }
              }}
              className="cursor-pointer group focus:outline-none"
            >
              {isSelected && (
                <circle
                  cx={p.x}
                  cy={p.y}
                  r="10"
                  fill={sty.fill}
                  fillOpacity="0.24"
                  stroke={sty.ring}
                  strokeWidth="1.2"
                />
              )}
              <circle
                cx={p.x}
                cy={p.y}
                r={isSelected ? "5.2" : inLondon ? "3.2" : "4.2"}
                fill={sty.fill}
                stroke="#081224"
                strokeWidth="1.4"
              />
              {!inLondon && (
                <text
                  x={labelX}
                  y={labelY}
                  fill={isSelected ? "#f8fafc" : "#cbd5e1"}
                  fontSize="8.8"
                  fontWeight={isSelected ? "700" : "500"}
                  textAnchor={st.provider_location_id === 804 ? "end" : "start"}
                >
                  {st.name.split(" - ")[0]}
                </text>
              )}
              <title>{`${st.name} (${sty.shortLabel} at Q1 2024 cutoff)`}</title>
            </g>
          );
        })}

        {/* Greater London Zoom Inset Card (top right over North Sea) */}
        <g>
          <rect
            x="296"
            y="16"
            width="140"
            height="170"
            rx="8"
            fill="#0c182e"
            stroke="#334155"
            strokeWidth="1.1"
          />
          <text x="305" y="31" fill="#e2e8f0" fontSize="9.5" fontWeight="700">
            Greater London Inset
          </text>
          {/* Thames inside London inset */}
          <polyline
            points={[
              [-0.56, 51.43],
              [-0.31, 51.45],
              [-0.13, 51.5],
              [0.08, 51.49],
              [0.28, 51.48],
            ]
              .map(([lon, lat]) => {
                const pt = projectLondonInset(lat, lon);
                return `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`;
              })
              .join(" ")}
            fill="none"
            stroke="#38bdf8"
            strokeOpacity="0.4"
            strokeWidth="2.2"
            strokeLinecap="round"
          />
          <text x="348" y="118" fill="#38bdf8" fillOpacity="0.45" fontSize="7.5">
            R. Thames
          </text>

          {londonStations.map((st) => {
            const p = projectLondonInset(st.latitude, st.longitude);
            const isSelected = st.id === selectedStationId;
            const sty = statusStyle(st.status);
            const off = londonLabelOffsets[st.provider_location_id] ?? {
              dx: 7,
              dy: 3,
              short: st.name.split(" - ")[0],
            };
            return (
              <g
                key={`ldn-${st.id}`}
                role="button"
                tabIndex={0}
                aria-label={`Select ${st.name} in Greater London inset`}
                onClick={() => onSelectStation(st.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectStation(st.id);
                  }
                }}
                className="cursor-pointer focus:outline-none"
              >
                {isSelected && (
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r="10"
                    fill={sty.fill}
                    fillOpacity="0.25"
                    stroke={sty.ring}
                    strokeWidth="1.2"
                  />
                )}
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={isSelected ? "5.2" : "4.2"}
                  fill={sty.fill}
                  stroke="#0c182e"
                  strokeWidth="1.4"
                />
                <text
                  x={p.x + off.dx}
                  y={p.y + off.dy}
                  textAnchor={off.dx < 0 ? "end" : "start"}
                  fill={isSelected ? "#ffffff" : "#cbd5e1"}
                  fontSize="8.5"
                  fontWeight={isSelected ? "700" : "500"}
                >
                  {off.short}
                </text>
                <title>{`${st.name} (${sty.shortLabel} at Q1 2024 cutoff)`}</title>
              </g>
            );
          })}

          {/* Legend inside right rail below inset */}
          <g transform="translate(296, 198)">
            <rect
              x="0"
              y="0"
              width="140"
              height="68"
              rx="8"
              fill="#0c182e"
              stroke="#1e293b"
            />
            <text x="9" y="15" fill="#94a3b8" fontSize="8" fontWeight="600">
              HISTORICAL CUTOFF STATUS
            </text>
            <circle cx="14" cy="29" r="3.5" fill="#10b981" />
            <text x="23" y="32" fill="#cbd5e1" fontSize="8">
              Healthy at eval time
            </text>
            <circle cx="14" cy="43" r="3.5" fill="#f59e0b" />
            <text x="23" y="46" fill="#cbd5e1" fontSize="8">
              Late at eval time
            </text>
            <circle cx="14" cy="57" r="3.5" fill="#f43f5e" />
            <text x="23" y="60" fill="#cbd5e1" fontSize="8">
              Withheld (Gap &gt;12h)
            </text>
          </g>
        </g>
      </svg>
    </div>
  );
}
