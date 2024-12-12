"""Tests for the analyst guidance parsing."""

sample_reports = [
    {
        "headline": "JMP Securities Maintains Market Outperform on Ready Capital, Lowers Price Target to $9.5",
        "firm": "JMP Securities",
        "action": "Maintains",
        "guidance": "Market Outperform",
        "stock": "Ready Capital",
        "price_target_action": "Lowers",
        "price_target": 9.5,
    },
    {
        "headline": "Wells Fargo Maintains Equal-Weight on Bath & Body Works, Raises Price Target to $42",
        "firm": "Wells Fargo",
        "action": "Maintains",
        "guidance": "Equal-Weight",
        "stock": "Bath & Body Works",
        "price_target_action": "Raises",
        "price_target": 42,
    },
    {
        "headline": "Oppenheimer Maintains Outperform on Toll Brothers, Maintains $189 Price Target",
        "firm": "Oppenheimer",
        "action": "Maintains",
        "guidance": "Outperform",
        "stock": "Toll Brothers",
        "price_target_action": "Maintains",
        "price_target": 189,
    },
    {
        "headline": "JMP Securities Reiterates Market Outperform on Relay Therapeutics, Maintains $21 Price Target",
        "firm": "JMP Securities",
        "action": "Reiterates",
        "guidance": "Market Outperform",
        "stock": "Relay Therapeutics",
        "price_target_action": "Maintains",
        "price_target": 21,
    },
    {
        "headline": "Leerink Partners Downgrades LAVA Therapeutics to Market Perform, Lowers Price Target to $2",
        "firm": "Leerink Partners",
        "action": "Downgrades",
        "guidance": "Market Perform",
        "stock": "LAVA Therapeutics",
        "price_target_action": "Lowers",
        "price_target": 2,
    },
    {
        "headline": "Wells Fargo Initiates Coverage On Xencor with Overweight Rating, Announces Price Target of $37",
        "firm": "Wells Fargo",
        "action": "Initiates Coverage",
        "guidance": "Overweight",
        "stock": "Xencor",
        "price_target_action": "Announces",
        "price_target": 37,
    },
    {
        "headline": "Wells Fargo Upgrades Ares Management to Overweight, Raises Price Target to $212",
        "firm": "Wells Fargo",
        "action": "Upgrades",
        "guidance": "Overweight",
        "stock": "Ares Management",
        "price_target_action": "Raises",
        "price_target": 212,
    },
    {
        "headline": "Jones Trading Initiates Coverage On Hut 8 with Buy Rating, Announces Price Target of $36",
        "firm": "Jones Trading",
        "action": "Initiates Coverage",
        "guidance": "Buy",
        "stock": "Hut 8",
        "price_target_action": "Announces",
        "price_target": 36,
    },
]


def test_analyst_news():
    """Test the get_firm_and_action function."""
    from stocknews.analyst import AnalystNews

    for report in sample_reports:
        obj = AnalystNews(report["headline"])
        assert obj.firm == report["firm"]
        assert obj.action == report["action"]
        assert obj.guidance == report["guidance"]
        assert obj.stock == report["stock"]
        assert obj.price_target == report["price_target"]
        assert obj.price_target_action == report["price_target_action"]
