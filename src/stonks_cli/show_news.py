"""CLI formatted output for the ``feed`` command and dashboard panel."""

from rich.markup import escape

from stonks_cli.news_fetcher import NewsItem


def _quote_markup_attr(value: str) -> str:
    """Quote and escape a Rich markup attribute value."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_news(symbol: str, items: list[NewsItem]) -> str:
    """Build a plain-text news feed for *symbol*."""
    if not items:
        return f"No recent news found for {symbol.upper()}."

    header = f"Latest news: {symbol.upper()}"
    lines = [header, "=" * len(header)]
    for item in items:
        lines.append(f"\n{item.headline}")
        lines.append(f"   {item.source}  --  {item.published_at}")
        lines.append(f"   {item.url}")
    return "\n".join(lines)


def format_news_panel(items: list[NewsItem]) -> str:
    """Build Rich-markup text for the dashboard news panel.

    Each item occupies one line in the form:
    datetime  ticker  linked-headline  (source)
    """
    header = "[bold]News[/bold]"
    if not items:
        return f"{header}\n  [dim]No recent news.[/dim]"

    lines = [header]
    for item in items:
        headline = escape(item.headline)
        if item.url:
            headline = f"[link={_quote_markup_attr(item.url)}]{headline}[/link]"
        ticker = f"[bold]{escape(item.symbol)}[/bold] " if item.symbol else ""
        meta_prefix = f"  [dim]{escape(item.published_at)}[/dim] {ticker}"
        meta_suffix = f" [dim]({escape(item.source)})[/dim]"
        lines.append(f"{meta_prefix}{headline}{meta_suffix}")
    return "\n".join(lines)
