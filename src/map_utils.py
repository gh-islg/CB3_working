"""Reusable Folium mapping helpers for CB3 project notebooks and scripts."""

import json
from pathlib import Path

import branca.colormap as cm
import folium
import numpy as np
import pandas as pd
from folium.features import GeoJsonPopup, GeoJsonTooltip


def find_project_dir(required_dirs=("data", "docs")):
    """Find the project root by walking upward from the current directory."""
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if all((candidate / directory).exists() for directory in required_dirs):
            return candidate
    required = " and ".join(f"{directory}/" for directory in required_dirs)
    raise FileNotFoundError(f"Could not find project root with {required} folders")


def make_step_colormap(values, colors, caption, positive_values_only=False):
    """Create quantile-based color steps for tract choropleth maps."""
    numeric_values = pd.to_numeric(values, errors="coerce").dropna()
    if positive_values_only:
        numeric_values = numeric_values[numeric_values > 0]

    breaks = np.unique(
        np.quantile(numeric_values, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    )

    # Ensure at least one interval even if all tract values are identical.
    if len(breaks) == 1:
        breaks = np.array([breaks[0], breaks[0] + 1])

    interval_count = len(breaks) - 1
    selected_colors = colors[:interval_count]
    return cm.StepColormap(
        colors=selected_colors,
        index=breaks.tolist(),
        vmin=float(breaks[0]),
        vmax=float(breaks[-1]),
        caption=caption,
    )


def add_zero_value_legend(map_object, color, label):
    """Add a small fixed-position legend item for zero or unavailable values."""
    legend_html = f"""
    <div style="
        position: fixed;
        bottom: 70px;
        left: 50px;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        border: 1px solid #777;
        border-radius: 4px;
        padding: 6px 9px;
        font: 12px Arial, sans-serif;">
      <span style="
          display:inline-block;
          width:14px;
          height:14px;
          margin-right:6px;
          vertical-align:middle;
          background:{color};
          border:1px solid #777;"></span>
      {label}
    </div>
    """
    map_object.get_root().html.add_child(folium.Element(legend_html))


def format_metric_value(value, unit):
    """Format metric values for map tooltips, popups, and panels."""
    if pd.isna(value):
        return "Not available"
    if unit == "%":
        return f"{value:,.1f}%"
    if unit == "$":
        return f"${value:,.0f}"
    return f"{value:,.0f}{unit}"


def add_map_title(map_object, title, subtitle):
    """Add a fixed map title box."""
    title_html = f"""
    <div style="
        position: fixed;
        top: 10px;
        left: 50px;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        border: 1px solid #777;
        border-radius: 4px;
        padding: 8px 12px;
        max-width: 520px;
        font-family: Arial, sans-serif;">
      <div style="font-size:16px;font-weight:700;">{title}</div>
      <div style="font-size:12px;margin-top:3px;">{subtitle}</div>
    </div>
    """
    map_object.get_root().html.add_child(folium.Element(title_html))


def make_base_map(geodataframe, tiles="CartoDB positron"):
    """Create a Folium basemap fitted to a GeoDataFrame's bounds."""
    map_object = folium.Map(
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
    )
    folium.TileLayer(
        tiles=tiles,
        name="Basemap",
        control=False,
    ).add_to(map_object)
    min_x, min_y, max_x, max_y = geodataframe.total_bounds
    map_object.fit_bounds([[min_y, min_x], [max_y, max_x]])
    return map_object


def json_ready(value):
    """Convert pandas/numpy values to JSON-safe Python scalars."""
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer, np.floating)):
        return float(value)
    return value


def add_metric_layer(
    map_object,
    tracts,
    metric,
    spec,
    show=True,
    add_legend=True,
    overlay=True,
    on_each_feature=None,
):
    """Add a styled choropleth GeoJSON layer for one metric to a Folium map.

    Parameters
    ----------
    map_object : folium.Map
    tracts : GeoDataFrame
        Must contain ``metric``, ``GEOID``, ``tract_label``, ``nta_name``, and ``geometry``.
    metric : str
        Column name in ``tracts`` to map.
    spec : dict
        Keys: ``dimension`` (layer name), ``label`` (tooltip alias), ``unit``,
        ``palette`` (list of hex colors). Optional: ``positive_values_only`` (bool),
        ``zero_color`` (hex), ``zero_label`` (str shown in the zero-value legend).
    show : bool
        Whether the layer is visible on load.
    add_legend : bool
        Whether to attach the colormap legend to the map.
    overlay : bool
        True → checkbox (overlay); False → radio button (base layer).
    on_each_feature : JsCode or None
        Passed directly to folium.GeoJson for per-feature JS callbacks
        (e.g. ``PROFILE_CLICK_CALLBACK`` from ``src.demographic_profile_panel``).
    """
    positive_values_only = spec.get("positive_values_only", False)
    zero_color = spec.get("zero_color", "#d9d9d9")

    colormap = make_step_colormap(
        tracts[metric],
        spec["palette"],
        spec["label"],
        positive_values_only=positive_values_only,
    )

    layer_data = tracts[["GEOID", "tract_label", "nta_name", metric, "geometry"]].copy()
    display_field = f"{metric}_display"
    layer_data[display_field] = layer_data[metric].map(
        lambda value: format_metric_value(value, spec["unit"])
    )

    def style_function(feature):
        value = feature["properties"].get(metric)
        if value is None:
            fill_color = "#d9d9d9"
        elif positive_values_only and value == 0:
            fill_color = zero_color
        else:
            fill_color = colormap(value)
        return {"fillColor": fill_color, "color": "#4d4d4d", "weight": 1, "fillOpacity": 0.72}

    layer = folium.GeoJson(
        data=layer_data.to_json(),
        name=spec["dimension"],
        overlay=overlay,
        show=show,
        style_function=style_function,
        highlight_function=lambda feature: {
            "weight": 3,
            "color": "#111111",
            "fillOpacity": 0.86,
        },
        tooltip=GeoJsonTooltip(
            fields=["tract_label", "nta_name", display_field],
            aliases=["Census tract", "NTA", spec["label"]],
            localize=True,
            sticky=False,
        ),
        popup=GeoJsonPopup(
            fields=["GEOID", "tract_label", "nta_name", display_field],
            aliases=["GEOID", "Census tract", "NTA", spec["label"]],
            localize=True,
        ),
        on_each_feature=on_each_feature,
    )
    layer.add_to(map_object)

    if add_legend:
        colormap.add_to(map_object)
        if positive_values_only:
            add_zero_value_legend(
                map_object,
                zero_color,
                spec.get("zero_label", "Zero"),
            )

    return layer


def add_dynamic_metric_legend(map_object, legend_payload, initial_metric_name, legend_id="metric-legend"):
    """Inject a fixed-position legend that updates when the active base layer changes.

    Parameters
    ----------
    map_object : folium.Map
    legend_payload : dict
        Keyed by metric dimension name (the layer-control label). Each value is a
        dict with keys ``title``, ``subtitle``, and ``entries`` (list of
        ``{"color": hex, "label": str}`` dicts).
    initial_metric_name : str
        The dimension name shown when the map first loads.
    legend_id : str
        CSS ``id`` for the legend ``<div>``. Use a unique value per page if multiple
        dynamic legends could ever appear in the same HTML file.

    Notes
    -----
    Folium initializes the Leaflet map variable in a ``<script>`` block placed
    *after* ``</body>``, so any inline legend script that references the map
    variable by name would throw a ``ReferenceError``. Both the ``baselayerchange``
    event listener and the radio-input fallback are therefore deferred into a
    ``setTimeout`` and the map is accessed via ``window["<var_name>"]`` to avoid
    a hard reference error if the timeout fires before map initialization completes.
    """
    safe_id = legend_id.replace("-", "_")
    js_data_var = f"cb3LegendData_{safe_id}"
    js_fn_name = f"updateCb3Legend_{safe_id}"
    map_var = map_object.get_name()

    legend_html = f"""
    <style>
      #{legend_id} {{
        position: fixed;
        left: 50px;
        bottom: 35px;
        z-index: 9999;
        width: 260px;
        box-sizing: border-box;
        padding: 10px 12px;
        background: rgba(255,255,255,0.94);
        border: 1px solid #777;
        border-radius: 4px;
        font-family: Arial, sans-serif;
        color: #222;
      }}
      #{legend_id} .legend-title {{
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 3px;
      }}
      #{legend_id} .legend-subtitle {{
        font-size: 11px;
        line-height: 1.25;
        color: #555;
        margin-bottom: 8px;
      }}
      #{legend_id} .legend-row {{
        display: flex;
        align-items: center;
        gap: 7px;
        margin: 4px 0;
        font-size: 11px;
      }}
      #{legend_id} .legend-swatch {{
        width: 15px;
        height: 15px;
        flex: 0 0 15px;
        border: 1px solid #777;
      }}
    </style>
    <div id="{legend_id}"></div>
    <script>
      const {js_data_var} = {json.dumps(legend_payload)};
      function {js_fn_name}(metricName) {{
        const legend = document.getElementById("{legend_id}");
        const data = {js_data_var}[metricName];
        if (!legend || !data) return;
        const rows = data.entries.map(entry => `
          <div class="legend-row">
            <span class="legend-swatch" style="background:${{entry.color}}"></span>
            <span>${{entry.label}}</span>
          </div>
        `).join("");
        legend.innerHTML = `
          <div class="legend-title">${{data.title}}</div>
          <div class="legend-subtitle">${{data.subtitle}}</div>
          ${{rows}}
        `;
      }}

      {js_fn_name}({json.dumps(initial_metric_name)});

      // Defer both the Leaflet event listener and the radio-input fallback until
      // after the map variable is initialized (map init script runs after </body>).
      setTimeout(function() {{
        var mapObj = window["{map_var}"];
        if (mapObj) {{
          mapObj.on("baselayerchange", function(event) {{
            {js_fn_name}(event.name);
          }});
        }}
        document.querySelectorAll(".leaflet-control-layers-base label").forEach(function(label) {{
          var input = label.querySelector("input[type='radio']");
          var span = label.querySelector("span");
          if (input && span) {{
            input.addEventListener("change", function() {{
              {js_fn_name}(span.textContent.trim());
            }});
          }}
        }});
      }}, 500);
    </script>
    """
    map_object.get_root().html.add_child(folium.Element(legend_html))
