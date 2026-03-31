"""CLI formatted output for the ``feed`` command and dashboard panel."""

from stonks_cli.news_fetcher import NewsItem


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

    Each item occupies two lines: headline on the first, source/time/URL
    (dimmed) on the second.
    """
    header = "[bold]News[/bold]"
    if not items:
        return f"{header}\n  [dim]No recent news.[/dim]"

    lines = [header]
    for item in items:
        lines.append(f"  {item.headline}")
        lines.append(
            f"[dim]    {item.source}  --  {item.published_at}  --  {item.url}[/dim]"
        )
    return "\n".join(lines)
