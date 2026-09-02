import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # root v2

# sample_event.py
"""تست واقعی nlp_event.py با LLM واقعی."""

from langchain_ollama import ChatOllama
# from langchain_openai import ChatOpenAI

from agents.fundamental.data_fetcher import fetch_market_context
from core.models import EventAnalysisInput
from agents.fundamental.nlp_event import analyze_economic_event

# LLM choice
llm = ChatOllama(model="llama3.1", temperature=0.0)

# یا برای production:
# llm = ChatOpenAI(
#     model="meta-llama/llama-3.3-70b-instruct",
#     temperature=0.0,
#     base_url="https://openrouter.ai/api/v1",
#     api_key="YOUR_KEY",
# )

# Test: hot CPI release
event_input = EventAnalysisInput(
    title="CPI y/y",
    currency="USD",
    impact="High",
    category="CPI",
    actual=3.8,
    forecast=3.5,
    previous=3.6,
    raw_actual_str="3.8%",
    raw_forecast_str="3.5%",
    raw_previous_str="3.6%",
)

# Fetch market context (uses upgraded historical_std)
market_context = fetch_market_context(
    ticker="EURUSD=X",
    currency="EUR",
    event_title="CPI y/y",
    event_currency="USD",
    actual=3.8,
    forecast=3.5,
    previous=3.6,
)

# Run analysis
interpretation, signal = analyze_economic_event(
    event_input=event_input,
    market_context=market_context,
    ticker="EURUSD=X",
    llm=llm,
    persist=True,
)

# Show results
print("\n" + "=" * 60)
print("  INTERPRETATION")
print("=" * 60)
print(f"  Direction:              {interpretation.direction}")
print(f"  Sentiment score:        {interpretation.nlp_sentiment_score:.3f}")
print(f"  Surprise interp:        {interpretation.surprise_interpretation}")
print(f"  Momentum:               {interpretation.momentum_vs_previous}")
print(f"  Economic implication:   {interpretation.economic_implication}")
print(f"  Impact horizon:         {interpretation.impact_horizon}")
print(f"  Cross-assets:           {interpretation.cross_assets}")
print(f"  Consistent w/ rule:     {interpretation.is_consistent_with_event_type}")
print(f"  Quant alignment:        {interpretation.quantitative_alignment:.2f}")
print()
print(f"  Reasoning:")
print(f"  {interpretation.reasoning}")

print("\n" + "=" * 60)
print("  SIGNAL")
print("=" * 60)
print(f"  Direction:              {signal.direction}")
print(f"  Final score:            {signal.final_score:.4f}")
print(f"  Confidence:             {signal.confidence:.2%}")
print(f"  Tradable:               {signal.is_tradable}")
print(f"  Vol level:              {signal.expected_volatility_level}")
print(f"  Half-life:              {signal.signal_half_life_mins} min")
print(f"  Cross-asset signals:    {signal.cross_asset_signals}")
print()
print(f"  Event impact weight:    {signal.event_impact_weight}")
print(f"  Data quality factor:    {signal.data_quality_factor:.2f}")
print(f"  Historical std used:    {signal.historical_std_used:.4f}")
print(f"  Std source:             {signal.historical_std_source}")
print(f"  Std reliable:           {signal.historical_std_reliable}")