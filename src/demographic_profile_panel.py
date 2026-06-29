"""Reusable tract demographic profile panel for Folium maps."""

import json

import folium
import pandas as pd
from folium.features import GeoJsonTooltip
from folium.utilities import JsCode

from src.map_utils import format_metric_value, json_ready


DEFAULT_DEMOGRAPHIC_PROFILE_SPECS = {
    "median_household_income": {
        "label": "Median household income",
        "unit": "$",
        "group": "Income",
    },
    "age_0_to_19_share": {
        "label": "Age 0-19",
        "unit": "%",
        "group": "Age",
    },
    "age_20_to_64_share": {
        "label": "Age 20-64",
        "unit": "%",
        "group": "Age",
    },
    "age_65_plus_share": {
        "label": "Age 65+",
        "unit": "%",
        "group": "Age",
    },
    "white_non_hispanic_share": {
        "label": "White non-Hispanic",
        "unit": "%",
        "group": "Race and ethnicity",
    },
    "black_non_hispanic_share": {
        "label": "Black non-Hispanic",
        "unit": "%",
        "group": "Race and ethnicity",
    },
    "asian_non_hispanic_share": {
        "label": "Asian non-Hispanic",
        "unit": "%",
        "group": "Race and ethnicity",
    },
    "hispanic_share": {
        "label": "Hispanic, any race",
        "unit": "%",
        "group": "Race and ethnicity",
    },
    "poverty_rate": {
        "label": "Poverty rate",
        "unit": "%",
        "group": "Economic vulnerability",
    },
    "senior_poverty_rate": {
        "label": "Senior poverty rate",
        "unit": "%",
        "group": "Economic vulnerability",
    },
    "lep_household_share": {
        "label": "LEP household share",
        "unit": "%",
        "group": "Language access",
    },
}


PROFILE_CLICK_CALLBACK = JsCode(
    """
function(feature, layer) {
    layer.on("click", function() {
        if (feature.properties && feature.properties.GEOID && window.renderProfile) {
            window.renderProfile(feature.properties.GEOID);
        }
    });
}
"""
)


def add_profile_click_layer(map_object, tracts):
    """Add a high-priority invisible tract layer for profile-panel clicks."""
    click_data = tracts[["GEOID", "tract_label", "nta_name", "geometry"]].copy()

    folium.map.CustomPane(
        "profile_click_pane",
        z_index=900,
    ).add_to(map_object)

    click_layer = folium.GeoJson(
        data=click_data.to_json(),
        name="Tract profile click target",
        show=True,
        control=False,
        pane="profile_click_pane",
        style_function=lambda feature: {
            "fillColor": "#ffffff",
            "color": "#000000",
            "weight": 0,
            "fillOpacity": 0.01,
            "opacity": 0,
        },
        highlight_function=lambda feature: {
            "weight": 3,
            "color": "#111111",
            "fillOpacity": 0.04,
            "opacity": 1,
        },
        tooltip=GeoJsonTooltip(
            fields=["tract_label", "nta_name"],
            aliases=["Census tract", "NTA"],
            sticky=False,
        ),
    ).add_to(map_object)
    return click_layer


def add_demographic_profile_panel(
    map_object,
    tracts,
    profile_specs=DEFAULT_DEMOGRAPHIC_PROFILE_SPECS,
    click_layer=None,
    panel_title="CB3 Housing & Affordability",
    panel_subtitle=(
        "Use the layer control on the map to switch metrics. Click a tract to "
        "update the demographic profile below. Gray ticks show the distribution "
        "across CB3 tracts; the red marker shows the selected tract."
    ),
):
    """Add a right-side profile panel that updates when a tract is clicked."""
    profile_columns = [
        "GEOID",
        "tract_label",
        "nta_name",
        *profile_specs.keys(),
    ]
    profile_records = []
    for record in tracts[profile_columns].to_dict(orient="records"):
        profile_records.append({key: json_ready(value) for key, value in record.items()})

    distributions = {}
    for metric in profile_specs:
        distributions[metric] = [
            float(value)
            for value in pd.to_numeric(tracts[metric], errors="coerce").dropna()
        ]

    panel_html = f"""
    <style>
      html, body {{ height: 100%; margin: 0; }}
      #{map_object.get_name()} {{
        position: fixed !important;
        top: 0;
        bottom: 0;
        left: 0;
        right: 36%;
        width: 64% !important;
        height: 100% !important;
      }}
      #tract-demographic-profile {{
        position: fixed;
        top: 0;
        right: 0;
        width: 36%;
        height: 100%;
        z-index: 9998;
        overflow-y: auto;
        box-sizing: border-box;
        padding: 18px 18px 28px 18px;
        background: #ffffff;
        border-left: 1px solid #bdbdbd;
        font-family: Arial, sans-serif;
        color: #222;
      }}
      #tract-demographic-profile h2 {{
        margin: 0 0 4px 0;
        font-size: 20px;
      }}
      #tract-demographic-profile .subtitle {{
        margin-bottom: 14px;
        color: #555;
        font-size: 12px;
        line-height: 1.35;
      }}
      #tract-demographic-profile h3 {{
        margin: 18px 0 8px 0;
        padding-top: 10px;
        border-top: 1px solid #e0e0e0;
        font-size: 14px;
        color: #333;
      }}
      #tract-demographic-profile .metric-row {{
        margin-bottom: 14px;
      }}
      #tract-demographic-profile .metric-label {{
        display: flex;
        justify-content: space-between;
        gap: 12px;
        font-size: 12px;
        margin-bottom: 4px;
      }}
      #tract-demographic-profile .metric-value {{
        font-weight: 700;
      }}
      #tract-demographic-profile svg {{
        width: 100%;
        height: 42px;
        overflow: visible;
      }}
      #tract-demographic-profile .axis-labels {{
        display: flex;
        justify-content: space-between;
        font-size: 10px;
        color: #666;
        margin-top: -2px;
      }}
      #tract-demographic-profile .missing {{
        color: #777;
        font-style: italic;
        font-size: 12px;
      }}
    </style>
    <div id="tract-demographic-profile">
      <h2>{panel_title}</h2>
      <div class="subtitle">{panel_subtitle}</div>
      <div id="tract-demographic-profile-content"></div>
    </div>
    <script>
      const cb3ProfileData = {json.dumps(profile_records)};
      const cb3ProfileSpecs = {json.dumps(profile_specs)};
      const cb3ProfileDistributions = {json.dumps(distributions)};

      function formatProfileValue(value, unit) {{
        if (value === null || value === undefined || Number.isNaN(value)) {{
          return "Not available";
        }}
        if (unit === "%") {{
          return `${{Number(value).toFixed(1)}}%`;
        }}
        if (unit === "$") {{
          return `$${{Math.round(Number(value)).toLocaleString()}}`;
        }}
        return Math.round(Number(value)).toLocaleString();
      }}

      function scaledX(value, values, width) {{
        const minValue = Math.min(...values);
        const maxValue = Math.max(...values);
        if (maxValue === minValue) return width / 2;
        return ((value - minValue) / (maxValue - minValue)) * width;
      }}

      function distributionSvg(metric, selectedValue) {{
        const values = cb3ProfileDistributions[metric] || [];
        if (selectedValue === null || selectedValue === undefined || values.length === 0) {{
          return `<div class="missing">Distribution unavailable for this tract.</div>`;
        }}
        const width = 320;
        const height = 36;
        const baseY = 24;
        const ticks = values.map(value => {{
          const x = scaledX(value, values, width);
          return `<line x1="${{x}}" x2="${{x}}" y1="16" y2="30" stroke="#bdbdbd" stroke-width="1" />`;
        }}).join("");
        const selectedX = scaledX(selectedValue, values, width);
        const minValue = Math.min(...values);
        const maxValue = Math.max(...values);
        return `
          <svg viewBox="0 0 ${{width}} ${{height}}" preserveAspectRatio="none" role="img">
            <line x1="0" x2="${{width}}" y1="${{baseY}}" y2="${{baseY}}" stroke="#d9d9d9" stroke-width="1" />
            ${{ticks}}
            <line x1="${{selectedX}}" x2="${{selectedX}}" y1="7" y2="33" stroke="#d7301f" stroke-width="3" />
            <circle cx="${{selectedX}}" cy="7" r="4" fill="#d7301f" />
          </svg>
          <div class="axis-labels"><span>${{formatProfileValue(minValue, cb3ProfileSpecs[metric].unit)}}</span><span>${{formatProfileValue(maxValue, cb3ProfileSpecs[metric].unit)}}</span></div>
        `;
      }}

      function renderProfile(geoid) {{
        const tract = cb3ProfileData.find(row => row.GEOID === geoid);
        const container = document.getElementById("tract-demographic-profile-content");
        if (!tract || !container) return;

        let html = `
          <div class="subtitle"><strong>Census tract ${{tract.tract_label}}</strong><br>${{tract.nta_name || ""}}<br>GEOID ${{tract.GEOID}}</div>
        `;
        const groups = [...new Set(Object.values(cb3ProfileSpecs).map(spec => spec.group))];
        groups.forEach(group => {{
          html += `<h3>${{group}}</h3>`;
          Object.entries(cb3ProfileSpecs).forEach(([metric, spec]) => {{
            if (spec.group !== group) return;
            const value = tract[metric];
            html += `
              <div class="metric-row">
                <div class="metric-label"><span>${{spec.label}}</span><span class="metric-value">${{formatProfileValue(value, spec.unit)}}</span></div>
                ${{distributionSvg(metric, value)}}
              </div>
            `;
          }});
        }});
        container.innerHTML = html;
      }}
      window.renderProfile = renderProfile;

      const profileClickLayerName = {json.dumps(click_layer.get_name() if click_layer is not None else None)};

      function getProfileClickLayer() {{
        if (!profileClickLayerName) return null;
        return window[profileClickLayerName] || null;
      }}

      function wireProfileClicks() {{
        const mapObject = {map_object.get_name()};
        const profileClickLayer = getProfileClickLayer();
        function attach(layer) {{
          if (layer.feature && layer.feature.properties && layer.feature.properties.GEOID) {{
            if (layer._cb3ProfileClickHandler) {{
              layer.off("click", layer._cb3ProfileClickHandler);
            }}
            layer._cb3ProfileClickHandler = function() {{
              renderProfile(layer.feature.properties.GEOID);
            }};
            layer.on("click", layer._cb3ProfileClickHandler);
          }}
          if (layer.eachLayer) {{
            layer.eachLayer(attach);
          }}
        }}
        mapObject.eachLayer(attach);
        if (profileClickLayer) {{
          profileClickLayer.eachLayer(attach);
          profileClickLayer.bringToFront();
        }}
      }}

      setTimeout(function() {{
        wireProfileClicks();
        if (cb3ProfileData.length > 0) {{
          renderProfile(cb3ProfileData[0].GEOID);
        }}
      }}, 500);
      {map_object.get_name()}.on("baselayerchange", function(event) {{
        setTimeout(wireProfileClicks, 100);
      }});
      {map_object.get_name()}.on("overlayadd", function() {{
        setTimeout(wireProfileClicks, 100);
      }});
      {map_object.get_name()}.on("overlayremove", function() {{
        setTimeout(wireProfileClicks, 100);
      }});
    </script>
    """
    map_object.get_root().html.add_child(folium.Element(panel_html))
