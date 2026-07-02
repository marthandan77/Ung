from __future__ import annotations

import json


def tradingview_ung_chart_html(symbol: str = "AMEX:UNG") -> str:
    settings = {
        "autosize": True,
        "symbol": symbol,
        "interval": "5",
        "timezone": "America/New_York",
        "theme": "light",
        "style": "1",
        "locale": "en",
        "enable_publishing": False,
        "allow_symbol_change": False,
        "calendar": False,
        "support_host": "https://www.tradingview.com",
    }
    payload = json.dumps(settings, sort_keys=True)
    return f"""
    <div class="tradingview-widget-container" style="height:520px;width:100%;">
      <div class="tradingview-widget-container__widget" style="height:calc(100% - 32px);width:100%;"></div>
      <div class="tradingview-widget-copyright">
        <a href="https://www.tradingview.com/symbols/AMEX-UNG/" rel="noopener nofollow" target="_blank">
          <span class="blue-text">UNG chart by TradingView</span>
        </a>
      </div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
      {payload}
      </script>
    </div>
    """
