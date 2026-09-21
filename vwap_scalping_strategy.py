"""
VWAP Scalping Strategy Implementation

This script implements a VWAP-based scalping strategy with RSI and Bollinger Bands filters.
It processes forex data, calculates technical indicators, and generates trading signals.
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings
warnings.filterwarnings('ignore')


def load_and_preprocess_data(file_path):
    """
    Load and preprocess the forex data.
    
    Args:
        file_path (str): Path to the CSV file containing OHLCV data
        
    Returns:
        pd.DataFrame: Preprocessed dataframe with datetime index
    """
    # Load data
    df = pd.read_csv(file_path)
    
    # Clean and convert datetime
    df["Gmt time"] = df["Gmt time"].str.replace(".000", "")
    df['Gmt time'] = pd.to_datetime(df['Gmt time'], format='%d.%m.%Y %H:%M:%S')
    
    # Set datetime as index
    df.set_index("Gmt time", inplace=True)
    
    # Remove rows where High equals Low (no price movement)
    df = df[df.High != df.Low]
    
    return df


def calculate_technical_indicators(df):
    """
    Calculate technical indicators: VWAP, RSI, and Bollinger Bands.
    
    Args:
        df (pd.DataFrame): DataFrame with OHLCV data
        
    Returns:
        pd.DataFrame: DataFrame with added technical indicators
    """
    # Calculate VWAP
    df["VWAP"] = ta.vwap(df.High, df.Low, df.Close, df.Volume)
    
    # Calculate RSI
    df['RSI'] = ta.rsi(df.Close, length=16)
    
    # Calculate Bollinger Bands
    my_bbands = ta.bbands(df.Close, length=14, std=2.0)
    df = df.join(my_bbands)
    
    return df


def generate_vwap_signal(df, backcandles=15):
    """
    Generate VWAP signals based on price action relative to VWAP.
    
    Signal logic:
    - 3: Both bullish and bearish conditions met (indecisive)
    - 2: Bullish condition met (all candles have Open/Close <= VWAP)
    - 1: Bearish condition met (all candles have Open/Close >= VWAP)
    - 0: No clear signal
    
    Args:
        df (pd.DataFrame): DataFrame with VWAP column
        backcandles (int): Number of candles to look back for signal confirmation
        
    Returns:
        pd.DataFrame: DataFrame with added VWAPSignal column
    """
    VWAPsignal = [0] * len(df)
    
    for row in range(backcandles, len(df)):
        upt = 1  # Bullish flag
        dnt = 1  # Bearish flag
        
        for i in range(row - backcandles, row + 1):
            # Check if any candle has Open or Close above VWAP (bullish violation)
            if max(df.Open[i], df.Close[i]) >= df.VWAP[i]:
                dnt = 0
            # Check if any candle has Open or Close below VWAP (bearish violation)
            if min(df.Open[i], df.Close[i]) <= df.VWAP[i]:
                upt = 0
        
        # Assign signal based on flags
        if upt == 1 and dnt == 1:
            VWAPsignal[row] = 3  # Indecisive
        elif upt == 1:
            VWAPsignal[row] = 2  # Bullish
        elif dnt == 1:
            VWAPsignal[row] = 1  # Bearish
    
    df['VWAPSignal'] = VWAPsignal
    return df


def calculate_total_signal(df, backcandles=15):
    """
    Calculate the total signal combining VWAP signal, RSI, and Bollinger Bands.
    
    Signal logic:
    - Long signal (2): VWAP bullish AND price below lower BB AND RSI < 45
    - Short signal (1): VWAP bearish AND price above upper BB AND RSI > 55
    - No signal (0): Conditions not met
    
    Args:
        df (pd.DataFrame): DataFrame with VWAPSignal, RSI, and BB columns
        backcandles (int): Number of candles to look back (must match VWAP signal calculation)
        
    Returns:
        pd.DataFrame: DataFrame with added TotalSignal column
    """
    def TotalSignal(l):
        if (df.VWAPSignal[l] == 2 and  # VWAP bullish
            df.Close[l] <= df['BBL_14_2.0'][l] and  # Price below lower Bollinger Band
            df.RSI[l] < 45):  # RSI oversold
            return 2  # Long signal
        if (df.VWAPSignal[l] == 1 and  # VWAP bearish
            df.Close[l] >= df['BBU_14_2.0'][l] and  # Price above upper Bollinger Band
            df.RSI[l] > 55):  # RSI overbought
            return 1  # Short signal
        return 0  # No signal
    
    TotSignal = [0] * len(df)
    for row in range(backcandles, len(df)):
        TotSignal[row] = TotalSignal(row)
    
    df['TotalSignal'] = TotSignal
    return df


def generate_trading_signals(df):
    """
    Generate actual trading signals (entry points) from TotalSignal.
    
    Args:
        df (pd.DataFrame): DataFrame with TotalSignal column
        
    Returns:
        pd.DataFrame: DataFrame with added pointposbreak column for plotting
    """
    def pointposbreak(x):
        if x['TotalSignal'] == 1:  # Short signal
            return x['High'] + 1e-4  # Place arrow above high
        elif x['TotalSignal'] == 2:  # Long signal
            return x['Low'] - 1e-4   # Place arrow below low
        else:
            return np.nan  # No signal
    
    df['pointposbreak'] = df.apply(lambda row: pointposbreak(row), axis=1)
    return df


def plot_results(df, symbol="EURUSD"):
    """
    Create an interactive plotly chart showing price, indicators, and signals.
    
    Args:
        df (pd.DataFrame): DataFrame with all calculated data
        symbol (str): Trading symbol for chart title
    """
    # Create subplots
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=(f'{symbol} Price with VWAP and Signals', 'RSI', 'Signal Strength'),
        row_width=[0.6, 0.2, 0.2]
    )
    
    # Add candlestick chart
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df['Open'],
            high=df['High'],
            low=df['Low'],
            close=df['Close'],
            name="Price"
        ),
        row=1, col=1
    )
    
    # Add VWAP line
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df['VWAP'],
            line=dict(color='blue', width=1),
            name="VWAP"
        ),
        row=1, col=1
    )
    
    # Add Bollinger Bands
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df['BBU_14_2.0'],
            line=dict(color='gray', width=1, dash='dash'),
            name="Upper BB"
        ),
        row=1, col=1
    )
    
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df['BBL_14_2.0'],
            line=dict(color='gray', width=1, dash='dash'),
            name="Lower BB",
            fill='tonexty'
        ),
        row=1, col=1
    )
    
    # Add long signals (green arrows below price)
    long_signals = df[df['TotalSignal'] == 2]
    if not long_signals.empty:
        fig.add_trace(
            go.Scatter(
                x=long_signals.index,
                y=long_signals['pointposbreak'],
                mode='markers',
                marker=dict(symbol='triangle-up', size=10, color='green'),
                name='Long Signal'
            ),
            row=1, col=1
        )
    
    # Add short signals (red arrows above price)
    short_signals = df[df['TotalSignal'] == 1]
    if not short_signals.empty:
        fig.add_trace(
            go.Scatter(
                x=short_signals.index,
                y=short_signals['pointposbreak'],
                mode='markers',
                marker=dict(symbol='triangle-down', size=10, color='red'),
                name='Short Signal'
            ),
            row=1, col=1
        )
    
    # Add RSI
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df['RSI'],
            line=dict(color='purple', width=1),
            name="RSI"
        ),
        row=2, col=1
    )
    
    # Add RSI overbought/oversold lines
    fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)
    fig.add_hline(y=55, line_dash="dot", line_color="orange", row=2, col=1)  # Strategy threshold
    fig.add_hline(y=45, line_dash="dot", line_color="orange", row=2, col=1)  # Strategy threshold
    
    # Add signal strength (TotalSignal)
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df['TotalSignal'],
            mode='markers',
            marker=dict(
                size=8,
                color=df['TotalSignal'],
                colorscale=[[0, 'gray'], [1, 'red'], [2, 'green'], [3, 'yellow']],
                showscale=True
            ),
            name='Signal Strength'
        ),
        row=3, col=1
    )
    
    # Update layout
    fig.update_layout(
        title=f'{symbol} VWAP Scalping Strategy',
        xaxis_rangeslider_visible=False,
        height=900,
        showlegend=True
    )
    
    # Update y-axis labels
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="RSI", row=2, col=1)
    fig.update_yaxes(title_text="Signal", row=3, col=1)
    
    fig.show()


def save_results(df, output_path):
    """
    Save the processed data with signals to a CSV file.
    
    Args:
        df (pd.DataFrame): DataFrame with all calculated data
        output_path (str): Path to save the output CSV file
    """
    df.to_csv(output_path)
    print(f"Results saved to {output_path}")


def main():
    """
    Main function to execute the VWAP scalping strategy.
    """
    # Configuration
    INPUT_FILE = "EURUSD_Candlestick_5_M_ASK_30.09.2019-30.09.2022.csv"
    OUTPUT_FILE = "EURUSD_VWAP_Signals.csv"
    BACKCANDLES = 15
    
    try:
        print("Loading and preprocessing data...")
        df = load_and_preprocess_data(INPUT_FILE)
        print(f"Loaded {len(df)} rows of data")
        
        print("Calculating technical indicators...")
        df = calculate_technical_indicators(df)
        
        print("Generating VWAP signals...")
        df = generate_vwap_signal(df, BACKCANDLES)
        
        print("Calculating total signals...")
        df = calculate_total_signal(df, BACKCANDLES)
        
        print("Generating trading signals...")
        df = generate_trading_signals(df)
        
        # Print signal statistics
        signal_counts = df['TotalSignal'].value_counts()
        print("\nSignal Statistics:")
        print(f"Long signals (2): {signal_counts.get(2, 0)}")
        print(f"Short signals (1): {signal_counts.get(1, 0)}")
        print(f"No signals (0): {signal_counts.get(0, 0)}")
        print(f"Indecisive VWAP (3): {signal_counts.get(3, 0) if 'VWAPSignal' in df.columns else 'N/A'}")
        
        print("\nPlotting results...")
        plot_results(df)
        
        print("Saving results...")
        save_results(df, OUTPUT_FILE)
        
        print("Strategy execution completed successfully!")
        
    except FileNotFoundError:
        print(f"Error: Could not find the input file '{INPUT_FILE}'")
        print("Please ensure the file exists in the current directory or provide the correct path.")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        raise


if __name__ == "__main__":
    main()