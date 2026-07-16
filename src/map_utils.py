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
    overlay=True,
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
    overlay : bool
        True → checkbox (overlay); False → radio button (base layer), for
        grouping this as a mutually-exclusive option alongside demographic
        choropleth layers (see ``add_demographic_backdrop_layers``).
    """
    layer_data = tracts[["tract_label", "nta_name", "geometry"]]
    layer = folium.GeoJson(
        data=layer_data.to_json(),
        name=name,
        show=show,
        control=control,
        overlay=overlay,
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


def add_demographic_backdrop_layers(
    map_object, tracts, demographic_specs, none_option_label="No demographic background"
):
    """Add each demographic metric as a selectable (radio-button) choropleth
    base layer, plus a plain tract-outline option with no fill so viewers can
    turn off the demographic backdrop entirely. The first demographic layer
    is shown by default; the outline option is available but not shown.

    Parameters
    ----------
    map_object : folium.Map
    tracts : GeoDataFrame
        Must contain every column referenced by ``demographic_specs``, plus
        ``tract_label``, ``nta_name``, and ``geometry``.
    demographic_specs : dict
        Keyed by column name; drawn as selectable (radio-button) choropleth
        base layers, one shown at a time.
    none_option_label : str
        Layer-control label for the no-fill tract-outline option.
    """
    for index, demographic in enumerate(demographic_specs):
        add_metric_layer(
            map_object, tracts, demographic, demographic_specs[demographic],
            show=index == 0, add_legend=False, overlay=False,
        )
    add_tract_outline_layer(
        map_object, tracts, name=none_option_label, show=False, control=True, overlay=False,
    )


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
    scale_from_min=False,
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
    scale_from_min : bool
        By default, radius scales as sqrt(value / max_value) — suited to
        counts that meaningfully range down to zero (rodent inspections,
        violations), where a value near zero should look near-invisible.
        Set True for metrics that vary only modestly and never approach zero
        (e.g. a concentration like PM2.5, ranging ~6.5-10.6 ug/m3): radius
        instead scales as sqrt((value - min_value) / (max_value - min_value)),
        so the least and most extreme observed values stretch across the
        full min_radius-max_radius range instead of bunching near max_radius.
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
    min_value = plot_points[value_col].min() if scale_from_min else 0
    value_range = max_value - min_value

    def radius_for(value):
        if scale_from_min:
            if value_range <= 0:
                return max_radius
            # Clamp to 0 before the sqrt: the legend passes rounded reference
            # values (see add_bubble_size_legend) that can fall slightly below
            # the true observed min, which would otherwise make (value -
            # min_value) negative — and a negative number raised to the 0.5
            # power in Python silently returns a complex number rather than
            # raising, which breaks SVG rendering for that legend row.
            ratio = max(0.0, (value - min_value) / value_range)
            return min_radius + (max_radius - min_radius) * ratio ** 0.5
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
            map_object, label, unit, max_value, radius_for, color,
            bottom_offset=legend_bottom_offset,
            min_value=min_value if scale_from_min else None,
        )

    return layer


def add_bubble_size_legend(map_object, label, unit, max_value, radius_fn, color, bottom_offset=35, min_value=None):
    """Add a fixed-position legend showing three reference bubble sizes.

    By default shows fractions of the maximum (max, max/2, max/8), suited to
    counts that range down toward zero. Pass ``min_value`` (the layer's
    ``scale_from_min=True`` case) to show the observed min/midpoint/max
    instead — fractions of max wouldn't correspond to the actual radius
    scale when bubbles are sized relative to the observed minimum.
    """
    if min_value is not None:
        reference_values = sorted({round(v) for v in (min_value, (min_value + max_value) / 2, max_value)})
    else:
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


def add_categorical_point_layer(
    map_object,
    points,
    category_col,
    category_colors,
    name,
    lat_col="latitude",
    lon_col="longitude",
    tooltip_fields=None,
    tooltip_aliases=None,
    size=15,
    outline_color="#ffffff",
    show=True,
    overlay=True,
    add_legend=True,
    legend_bottom_offset=35,
):
    """Add fixed-size diamond markers colored by a categorical column.

    Unlike ``add_bubble_layer`` (which sizes round markers by a numeric
    magnitude and uses one fixed color), this draws same-size diamond
    markers colored per ``category_col`` value, for point data distinguished
    by category rather than magnitude (e.g. tree health status, violation
    type). The diamond shape keeps "round = sized by magnitude" and
    "diamond = categorical" as separate visual languages on the same map.
    Rows whose category isn't a key in ``category_colors`` are dropped.

    Parameters
    ----------
    map_object : folium.Map
    points : DataFrame
        Must contain ``lat_col``, ``lon_col``, and ``category_col``.
    category_col : str
        Column whose values select the marker color.
    category_colors : dict
        Maps each category value to a hex color; also defines the legend
        order (top to bottom) and which categories are plotted.
    name : str
        Layer-control name.
    tooltip_fields, tooltip_aliases : list or None
        Extra columns/aliases shown in the tooltip alongside ``category_col``.
    size : float
        Pixel width/height of each diamond marker (measured corner to corner).
    outline_color : str
        Marker stroke color, drawn as a halo around the fill so markers stay
        legible over any choropleth underneath.
    show : bool
        Whether the layer is visible on load.
    overlay : bool
        True → checkbox (overlay); False → radio button (base layer).
    add_legend : bool
        Whether to attach a categorical swatch legend to the map.
    legend_bottom_offset : int
        Pixels from the bottom of the map to the legend box. Increase if
        another legend already occupies the default position.
    """
    plot_points = points[points[category_col].isin(category_colors)].copy()

    fields = [category_col] + (tooltip_fields or [])
    aliases = [category_col.replace("_", " ").title()] + (tooltip_aliases or [])

    # A diamond is a square rotated 45deg; side length is set so the
    # corner-to-corner width/height equals `size`.
    side = round(size / 1.41421356, 1)

    layer = folium.FeatureGroup(name=name, show=show, control=overlay)
    for _, row in plot_points.iterrows():
        tooltip_lines = [f"{alias}: {row[field]}" for field, alias in zip(fields, aliases)]
        icon = folium.DivIcon(
            html=f"""
            <div style="
                width: {side}px;
                height: {side}px;
                background: {category_colors[row[category_col]]};
                border: 1.5px solid {outline_color};
                box-shadow: 0 0 1px 1px rgba(0,0,0,0.35);
                transform: rotate(45deg);
                box-sizing: border-box;
            "></div>
            """,
            icon_size=(side, side),
            icon_anchor=(side / 2, side / 2),
        )
        folium.Marker(
            location=[row[lat_col], row[lon_col]],
            icon=icon,
            tooltip=folium.Tooltip("<br>".join(tooltip_lines)),
        ).add_to(layer)
    layer.add_to(map_object)

    if add_legend:
        add_categorical_legend(map_object, name, category_colors, bottom_offset=legend_bottom_offset)

    return layer


def add_categorical_legend(map_object, title, category_colors, bottom_offset=35, shape="diamond"):
    """Add a fixed-position legend showing one swatch per category.

    Parameters
    ----------
    shape : str
        ``"diamond"`` (default) matches the diamond point markers drawn by
        ``add_categorical_point_layer``. Use ``"square"`` for categorical
        polygon/fill layers (e.g. an evacuation-zone choropleth), where a
        diamond swatch would misleadingly imply point markers. Use
        ``"circle"`` for the wedge colors drawn by
        ``build_grouped_bubble_map(..., combine_as_pie=True)``.
    """
    swatch_style = {
        "diamond": "transform:rotate(45deg);",
        "circle": "border-radius:50%;",
    }.get(shape, "")
    rows_html = "".join(
        f"""
        <div style="display:flex;align-items:center;gap:8px;margin:4px 0;">
          <span style="display:inline-block;width:10px;height:10px;background:{color};
                       border:1px solid #777;{swatch_style}"></span>
          <span>{label}</span>
        </div>
        """
        for label, color in category_colors.items()
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
      <div style="font-weight:700;margin-bottom:4px;">{title}</div>
      {rows_html}
    </div>
    """
    map_object.get_root().html.add_child(folium.Element(legend_html))


# Fixed color cycle for grouped bubble maps, so each related-metric layer
# stays a stable, distinguishable color regardless of how many layers are
# grouped together. The first two entries are unchanged from the original
# two-color cycle (e.g. paired expiration windows, violation classes); three
# more distinguishable colors are appended for groupings with more layers
# (e.g. AMI bands), with the metric list order controlling which color a
# given layer gets — put the most important layer first for a prominent color.
GROUPED_BUBBLE_COLORS = ["#252525", "#db3232", "#2166ac", "#33a02c", "#ff7f00"]


def build_metric_bubble_map(
    tracts,
    metric_specs,
    demographic_specs,
    metric,
    title,
    output_path,
    points=None,
    lat_col="tract_centroid_latitude",
    lon_col="tract_centroid_longitude",
    tooltip_fields=None,
    tooltip_aliases=None,
    extra_layers=None,
    color="#1a1a1a",
    subtitle=None,
):
    """Build and save a map with selectable demographic choropleth backdrops
    and one bubble layer for ``metric``, sized by magnitude.

    By default the bubble sits at each tract's centroid, since most metrics
    are only reported at the tract level. Metrics with native, finer-grained
    coordinates (e.g. individual building addresses) should pass their own
    ``points``/``lat_col``/``lon_col`` instead, since collapsing them to a
    tract centroid would discard real location detail.

    Parameters
    ----------
    tracts : GeoDataFrame
        Must contain ``geometry`` and every column referenced by
        ``metric_specs``/``demographic_specs`` (unless ``points`` is given
        for the bubble layer itself).
    metric_specs : dict
        Keyed by column name; values are specs as built for ``add_metric_layer``
        (``dimension``, ``label``, ``unit``, ``palette``, ``filename`` unused here).
    demographic_specs : dict
        Keyed by column name; drawn as selectable (radio-button) choropleth
        base layers, one shown at a time, for geographic/demographic context.
    metric : str
        Key into ``metric_specs`` for the bubble layer.
    title : str
        Map title text.
    output_path : Path
        Where to save the resulting HTML map.
    points : DataFrame or None
        Passed to ``add_bubble_layer``; defaults to ``tracts`` (tract centroids).
    tooltip_fields, tooltip_aliases : list or None
        Extra bubble tooltip fields; default to tract label/NTA.
    extra_layers : callable or None
        Optional ``f(map_object)`` invoked after the bubble layer but before
        ``LayerControl`` is added, for domain-specific overlays (e.g. an EJ
        Area overlay) that need to appear in the same control. Layers added
        after ``LayerControl`` render in a later script tag than the one that
        references them, which throws a JS ReferenceError and silently
        breaks the whole control — see Folium's own LayerControl docstring
        ("should be added last to the map").
    color : str
        Fixed marker fill color for the bubble layer; passed through to
        ``add_bubble_layer``. Defaults to its same near-black default.
    subtitle : str or None
        Overrides the auto-generated map subtitle ("Choropleth: ... Bubbles:
        ..."). Use when ``extra_layers`` adds other layer types (polygons,
        categorical points) that the auto-generated text wouldn't describe.
    """
    tooltip_fields = tooltip_fields if tooltip_fields is not None else ["tract_label", "nta_name"]
    tooltip_aliases = tooltip_aliases if tooltip_aliases is not None else ["Census tract", "NTA"]
    spec = metric_specs[metric]
    map_object = make_base_map(tracts)

    add_demographic_backdrop_layers(map_object, tracts, demographic_specs)

    add_bubble_layer(
        map_object,
        tracts if points is None else points,
        metric,
        spec["label"],
        spec["unit"],
        name=spec["dimension"],
        lat_col=lat_col,
        lon_col=lon_col,
        tooltip_fields=tooltip_fields,
        tooltip_aliases=tooltip_aliases,
        color=color,
        show=True,
        overlay=True,
    )
    if extra_layers is not None:
        extra_layers(map_object)
    add_map_title(
        map_object,
        title,
        subtitle if subtitle is not None else (
            "Choropleth: use the layer control to pick a demographic fill. "
            f"Bubbles: {spec['label']}."
        ),
    )
    add_zero_value_legend(map_object, "#d9d9d9", "Demographic data not available")
    folium.LayerControl(collapsed=False, position="topright").add_to(map_object)
    map_object.save(output_path)
    return map_object


def _add_combined_pie_layer(
    map_object, points, metrics, metric_specs, lat_col, lon_col,
    tooltip_fields, tooltip_aliases, min_radius, max_radius, name, legend_title, colors,
):
    """Draw one donut marker per row for ``build_grouped_bubble_map(...,
    combine_as_pie=True)``: sized by the summed value across ``metrics`` and
    split into wedges colored by ``colors``, in the same order as ``metrics``
    (so the first metric gets the most prominent color and the widest wedge
    share advantage at ties).

    ``name`` is the single layer-control label for the combined layer (each
    metric's own ``short_label`` would otherwise have to be concatenated,
    which reads poorly once there are more than two metrics). ``legend_title``
    labels the wedge-color legend; band rows use each metric's bare
    ``short_label`` (e.g. "0-30% AMI") since the shared context (e.g. "new
    construction") belongs in the title, not repeated on every row.
    """
    bands = [
        (metric, metric_specs[metric]["dimension"], colors[i % len(colors)])
        for i, metric in enumerate(metrics)
    ]
    plot_points = points.copy()
    plot_points["_pie_total"] = plot_points[metrics].sum(axis=1)
    plot_points = plot_points[plot_points["_pie_total"] > 0].copy()
    max_total = plot_points["_pie_total"].max()

    def radius_for(value): # formula same as other bubbles
        if max_total <= 0:
            return min_radius
        return min_radius + (max_radius - min_radius) * (value / max_total) ** 0.5

    fields = tooltip_fields or []
    aliases = tooltip_aliases or []
    layer = folium.FeatureGroup(name=name, show=True, control=True)
    for _, row in plot_points.iterrows():
        total = row["_pie_total"]
        radius = radius_for(total) # compute buiding's diameter/radius
        diameter = radius * 2

        # Conic-gradient stops so each wedge's angular share matches the
        # metric's share of this row's total.
        stops = []
        cumulative_pct = 0.0
        # for each AMI band/vategory, get fraction of the circle it should occupy
        for column, _, color in bands:
            share = row[column] / total
            if share <= 0:
                continue
            start_pct = cumulative_pct
            # convert to cumulative percentage range
            cumulative_pct += share * 100
            stops.append(f"{color} {start_pct:.2f}% {cumulative_pct:.2f}%")

        # tooltips
        tooltip_lines = [f"{alias}: {row[field]}" for field, alias in zip(fields, aliases)]
        tooltip_lines.append(f"Total: {int(total)}")
        for column, label, _ in bands:
            if row[column] > 0:
                tooltip_lines.append(f"{label}: {int(row[column])}")

        icon = folium.DivIcon(
            html=f"""
            <div style="
                width: {diameter}px;
                height: {diameter}px;
                border-radius: 50%;
                overflow: hidden;
                border: 1.5px solid #ffffff;
                box-shadow: 0 0 1px 1px rgba(0,0,0,0.35);
                box-sizing: border-box;
            "><div style="
                width: 100%;
                height: 100%;
                background: conic-gradient({", ".join(stops)});
            "></div></div>
            """,
            icon_size=(diameter, diameter),
            icon_anchor=(radius, radius),
        )
        folium.Marker(
            location=[row[lat_col], row[lon_col]],
            icon=icon,
            tooltip=folium.Tooltip("<br>".join(tooltip_lines)),
        ).add_to(layer)
    layer.add_to(map_object)

    add_bubble_size_legend(
        map_object, "Total", metric_specs[metrics[0]]["unit"], max_total, radius_for, "#555555",
    )
    add_categorical_legend(
        map_object, legend_title, {label: color for _, label, color in bands},
        bottom_offset=35 + 190, shape="circle",
    )


def build_grouped_bubble_map(
    tracts,
    metric_specs,
    demographic_specs,
    metrics,
    title,
    subtitle,
    output_path,
    points=None,
    lat_col="tract_centroid_latitude",
    lon_col="tract_centroid_longitude",
    tooltip_fields=None,
    tooltip_aliases=None,
    extra_layers=None,
    combine_as_pie=False,
    min_radius=8,
    max_radius=28,
    pie_name=None,
    pie_legend_title="Band",
    pie_colors=None,
):
    """Like ``build_metric_bubble_map``, but adds one bubble layer per metric
    in ``metrics`` so directly related metrics (e.g. two expiration windows,
    or two violation-severity classes) can be compared side by side on a
    single map instead of only one at a time. Each layer gets a distinct
    color from ``GROUPED_BUBBLE_COLORS`` and its own stacked size legend.

    Parameters mirror ``build_metric_bubble_map``, except ``metrics`` is a
    list of keys into ``metric_specs`` and ``subtitle`` is required (there is
    no single metric to derive default subtitle text from).

    combine_as_pie : bool
        By default (False), draws one full bubble layer per metric, each
        independently toggleable — suited to metrics that rarely co-occur at
        the same point (e.g. two non-overlapping expiration windows). Set
        True when the same point commonly has nonzero values across several
        metrics (e.g. AMI bands within one building): same-coordinate
        stacked circles would hide each other, so instead this draws one
        donut marker per point, sized by the summed total across ``metrics``
        and split into wedges colored by ``GROUPED_BUBBLE_COLORS``.
    pie_name : str or None
        Layer-control label for the combined layer when ``combine_as_pie`` is
        True (each metric's own ``short_label`` joined together reads poorly
        past two metrics, so this is a single explicit label instead).
        Defaults to ``title`` if not given.
    pie_legend_title : str
        Title for the wedge-color legend when ``combine_as_pie`` is True.
        Put shared context here (e.g. "New construction by AMI band") rather
        than repeating it on every band row.
    pie_colors : list of str or None
        Wedge color cycle when ``combine_as_pie`` is True. Defaults to
        ``GROUPED_BUBBLE_COLORS``; pass a different list for groupings where
        the shared categorical cycle doesn't fit (e.g. an ordered tier like
        AMI bands, better read as a single-hue ramp than distinct hues).
    """
    tooltip_fields = tooltip_fields if tooltip_fields is not None else ["tract_label", "nta_name"]
    tooltip_aliases = tooltip_aliases if tooltip_aliases is not None else ["Census tract", "NTA"]
    map_object = make_base_map(tracts)

    add_demographic_backdrop_layers(map_object, tracts, demographic_specs)

    if combine_as_pie:
        _add_combined_pie_layer(
            map_object, tracts if points is None else points, metrics, metric_specs,
            lat_col, lon_col, tooltip_fields, tooltip_aliases, min_radius, max_radius,
            pie_name or title, pie_legend_title, pie_colors or GROUPED_BUBBLE_COLORS,
        )
    else:
        for index, metric in enumerate(metrics):
            spec = metric_specs[metric]
            add_bubble_layer(
                map_object,
                tracts if points is None else points,
                metric,
                spec["label"],
                spec["unit"],
                name=spec["dimension"],
                lat_col=lat_col,
                lon_col=lon_col,
                tooltip_fields=tooltip_fields,
                tooltip_aliases=tooltip_aliases,
                color=GROUPED_BUBBLE_COLORS[index % len(GROUPED_BUBBLE_COLORS)],
                show=True,
                overlay=True,
                # Each bubble-size legend can be up to ~190px tall (title + 3
                # reference rows at the default max_radius=22), so layers must be
                # spaced further apart than that to avoid overlapping.
                legend_bottom_offset=35 + index * 230,
            )

    if extra_layers is not None:
        extra_layers(map_object)
    add_map_title(map_object, title, subtitle)
    add_zero_value_legend(map_object, "#d9d9d9", "Demographic data not available")
    folium.LayerControl(collapsed=False, position="topright").add_to(map_object)
    map_object.save(output_path)
    return map_object
