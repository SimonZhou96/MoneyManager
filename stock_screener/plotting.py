from typing import Dict

import pandas as pd
import plotly.graph_objects as go


def build_candlestick_chart(
    hist: pd.DataFrame,
    overlays: Dict[str, pd.Series],
    title: str,
) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=hist.index,
            open=hist["Open"],
            high=hist["High"],
            low=hist["Low"],
            close=hist["Close"],
            name="K线",
        )
    )

    for name, series in overlays.items():
        fig.add_trace(
            go.Scatter(
                x=hist.index,
                y=series,
                mode="lines",
                name=name,
            )
        )

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="价格",
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
