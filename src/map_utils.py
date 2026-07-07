"""Reusable Folium mapping helpers for CB3 project notebooks and scripts."""

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


def add_tract_outline_layer(
    map_object,
    tracts,
    name="Census tract boundaries",
    color="#999999",
    weight=1,
    show=True,
    control=False,
):
    """Add a plain, unfilled tract-boundary layer for context under point/bubble layers.

    Parameters
    ----------
    map_object : folium.Map
    tracts : GeoDataFrame
        Must contain ``tract_label``, ``nta_name``, and ``geometry``.
    name : str
        Layer-control name (only shown if ``control`` is True).
    color, weight : line style for the tract boundaries.
    show : bool
        Whether the layer is visible on load.
    control : bool
        Whether this layer appears as a toggle in the layer control. Defaults
        to False since it is meant as a fixed reference layer, not a dataset
        the viewer selects.
    """
    layer_data = tracts[["tract_label", "nta_name", "geometry"]]
    layer = folium.GeoJson(
        data=layer_data.to_json(),
        name=name,
        show=show,
        control=control,
        style_function=lambda feature: {
            "fillOpacity": 0,
            "color": color,
            "weight": weight,
        },
        tooltip=GeoJsonTooltip(
            fields=["tract_label", "nta_name"],
            aliases=["Census tract", "NTA"],
            sticky=False,
        ),
    )
    layer.add_to(map_object)
    return layer


def add_bubble_layer(
    map_object,
    points,
    value_col,
    label,
    unit,
    name=None,
    lat_col="latitude",
    lon_col="longitude",
    tooltip_fields=None,
    tooltip_aliases=None,
    color="#1a1a1a",
    outline_color="#ffffff",
    min_radius=5,
    max_radius=22,
    show=True,
    overlay=True,
    add_legend=True,
    legend_bottom_offset=35,
):
    """Add a sized-circle-marker layer for a point-level metric to a Folium map.

    One circle is drawn per row in ``points``. Radius scales with the square
    root of ``value_col`` so that circle *area* (not radius) is proportional to
    magnitude. Use this directly for metrics with native coordinates (e.g.
    building addresses); for metrics only available at the tract/NTA level,
    first attach polygon centroid coordinates to ``lat_col``/``lon_col`` and
    pass the result in.

    Parameters
    ----------
    map_object : folium.Map
    points : DataFrame
        Must contain ``lat_col``, ``lon_col``, and ``value_col``.
    value_col : str
        Column to size circles by.
    label : str
        Human-readable metric label used in tooltips and the size legend.
    unit : str
        Passed to ``format_metric_value`` for tooltip/legend text.
    name : str or None
        Layer-control name; defaults to ``label``.
    tooltip_fields, tooltip_aliases : list or None
        Extra columns/aliases shown in the tooltip alongside ``value_col``.
    color : str
        Fixed marker fill color for every circle in this layer. The default
        (near-black) plus the white ``outline_color`` halo keep bubbles
        legible over any of the demographic choropleth palettes underneath.
    outline_color : str
        Marker stroke color, drawn as a halo around the fill so bubbles stay
        visible regardless of the color scheme of an underlying choropleth.
    min_radius, max_radius : float
        Pixel radius bounds for the smallest and largest non-zero values.
    show : bool
        Whether the layer is visible on load.
    overlay : bool
        True → checkbox (overlay); False → radio button (base layer).
    add_legend : bool
        Whether to attach a bubble-size legend to the map.
    legend_bottom_offset : int
        Pixels from the bottom of the map to the legend box. Increase for each
        additional bubble layer on the same map so legends stack without
        overlapping.
    """
    layer_name = name or label
    plot_points = points[points[value_col] > 0].copy()

    max_value = plot_points[value_col].max()

    def radius_for(value):
        if max_value <= 0:
            return min_radius
        return min_radius + (max_radius - min_radius) * (value / max_value) ** 0.5

    fields = [value_col] + (tooltip_fields or [])
    aliases = [label] + (tooltip_aliases or [])

    layer = folium.FeatureGroup(name=layer_name, show=show, control=overlay)
    for _, row in plot_points.iterrows():
        tooltip_lines = [
            f"{alias}: {format_metric_value(row[field], unit) if field == value_col else row[field]}"
            for field, alias in zip(fields, aliases)
        ]
        folium.CircleMarker(
            location=[row[lat_col], row[lon_col]],
            radius=radius_for(row[value_col]),
            color=outline_color,
            weight=1.5,
            fill=True,
            fill_color=color,
            fill_opacity=0.75,
            tooltip=folium.Tooltip("<br>".join(tooltip_lines)),
        ).add_to(layer)
    layer.add_to(map_object)

    if add_legend and max_value > 0:
        add_bubble_size_legend(
            map_object, label, unit, max_value, radius_for, color, bottom_offset=legend_bottom_offset
        )

    return layer


def add_bubble_size_legend(map_object, label, unit, max_value, radius_fn, color, bottom_offset=35):
    """Add a fixed-position legend showing three reference bubble sizes."""
    reference_values = sorted(set(round(v) for v in (max_value, max_value / 2, max_value / 8) if v > 0))
    svg_size = 2 * radius_fn(max(reference_values))
    rows_html = "".join(
        f"""
        <div style="display:flex;align-items:center;gap:8px;margin:4px 0;">
          <svg width="{svg_size}" height="{svg_size}" style="flex:0 0 auto;">
            <circle cx="{svg_size / 2}" cy="{svg_size / 2}" r="{radius_fn(value)}"
                    fill="{color}" fill-opacity="0.75" stroke="#777" stroke-width="1" />
          </svg>
          <span>{format_metric_value(value, unit)}</span>
        </div>
        """
        for value in reversed(reference_values)
    )
    legend_html = f"""
    <div style="
        position: fixed;
        bottom: {bottom_offset}px;
        right: 20px;
        z-index: 9999;
        background: rgba(255,255,255,0.94);
        border: 1px solid #777;
        border-radius: 4px;
        padding: 8px 12px;
        font: 12px Arial, sans-serif;
        color: #222;">
      <div style="font-weight:700;margin-bottom:4px;">{label}</div>
      {rows_html}
    </div>
    """
    map_object.get_root().html.add_child(folium.Element(legend_html))
