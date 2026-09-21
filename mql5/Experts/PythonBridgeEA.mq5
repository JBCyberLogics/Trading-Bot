//+------------------------------------------------------------------+
//| PythonBridgeEA.mq5 - Enhanced with Trade Execution               |
//+------------------------------------------------------------------+
#property copyright "Your Name"
#property version   "1.10"
#property strict

#include <Zmq/Zmq.mqh>

input string PythonHost = "127.0.0.1"; // bind host; use "*" for all interfaces
input int    PythonPort = 5555;
input int    DataPort   = 5556;
input int    LivePort   = 5557;
input int    MagicNumber = 234001;
input bool   DebugLogging = true;
input int    MaxMessagesPerTick = 100;

Context context;
Socket *cmd_socket;
Socket *data_socket;
Socket *live_socket;

// --- helpers ---
string EscapeJson(const string s)
{
   string out = s;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\r", "\\r");
   StringReplace(out, "\n", "\\n");
   StringReplace(out, "\t", "\\t");
   return out;
}

int SkipWhitespace(const string s, int i)
{
   int len = StringLen(s);
   while(i < len)
   {
      int ch = StringGetCharacter(s, i);
      if(ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n') break;
      i++;
   }
   return i;
}

string Trim(const string s)
{
   string t = s;
   StringTrimLeft(t);
   StringTrimRight(t);
   return t;
}

bool ExtractValue(const string jsonStr, const string key, string &value, string &reason)
{
   string needle = "\"" + key + "\"";
   int keyPos = StringFind(jsonStr, needle);
   if(keyPos < 0)
   {
      reason = "Missing " + key;
      return false;
   }

   int colonPos = StringFind(jsonStr, ":", keyPos);
   if(colonPos < 0)
   {
      reason = "Missing ':' for " + key;
      return false;
   }

   int len = StringLen(jsonStr);
   int i = SkipWhitespace(jsonStr, colonPos + 1);
   if(i >= len)
   {
      reason = "Missing value for " + key;
      return false;
   }

   int ch = StringGetCharacter(jsonStr, i);
   if(ch == '"')
   {
      int j = i + 1;
      while(j < len)
      {
         int c = StringGetCharacter(jsonStr, j);
         if(c == '"') break;
         j++;
      }
      if(j >= len)
      {
         reason = "Unterminated string for " + key;
         return false;
      }
      value = StringSubstr(jsonStr, i + 1, j - i - 1);
      return true;
   }

   int j = i;
   while(j < len)
   {
      int c = StringGetCharacter(jsonStr, j);
      if(c == ',' || c == '}' || c == ']') break;
      j++;
   }
   value = Trim(StringSubstr(jsonStr, i, j - i));
   if(value == "")
   {
      reason = "Empty value for " + key;
      return false;
   }
   return true;
}

bool ExtractString(const string jsonStr, const string key, string &out, string &reason)
{
   if(!ExtractValue(jsonStr, key, out, reason)) return false;
   return true;
}

bool ExtractNumber(const string jsonStr, const string key, double &out, string &reason)
{
   string raw;
   if(!ExtractValue(jsonStr, key, raw, reason)) return false;
   out = StringToDouble(raw);
   return true;
}

bool TradingAllowed(string &reason)
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      reason = "Terminal trade not allowed";
      return false;
   }
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   {
      reason = "Account trade not allowed";
      return false;
   }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      reason = "MQL trade not allowed";
      return false;
   }
   return true;
}

string BuildAccountResponse()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin  = AccountInfoDouble(ACCOUNT_MARGIN);
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);

   return StringFormat(
      "{\"action\":\"ACCOUNT\",\"balance\":%.2f,\"equity\":%.2f,\"margin\":%.2f,\"free_margin\":%.2f}",
      balance,
      equity,
      margin,
      free_margin
   );
}

string BuildPositionsResponse()
{
   return "{\"action\":\"POSITIONS\",\"positions\":[]}";
}

string BuildSymbolsResponse()
{
   int total_selected = SymbolsTotal(true);
   bool use_selected = (total_selected > 0);
   int total = use_selected ? total_selected : SymbolsTotal(false);

   if(total <= 0)
      return "{\"action\":\"SYMBOLS\",\"source\":\"none\",\"count\":0,\"symbols\":[]}";

   string symbols = "[";
   int count = 0;
   for(int i = 0; i < total; i++)
   {
      string sym = SymbolName(i, use_selected);
      if(StringLen(sym) == 0)
         continue;

      if(count > 0)
         symbols += ",";

      symbols += "\"" + EscapeJson(sym) + "\"";
      count++;
   }
   symbols += "]";

   return StringFormat(
      "{\"action\":\"SYMBOLS\",\"source\":\"%s\",\"count\":%d,\"symbols\":%s}",
      use_selected ? "market_watch" : "all",
      count,
      symbols
   );
}

bool TimeframeFromString(const string tf_raw, ENUM_TIMEFRAMES &out_tf)
{
   string tf = tf_raw;
   StringToUpper(tf);

   if(tf == "M1")   { out_tf = PERIOD_M1; return true; }
   if(tf == "M2")   { out_tf = PERIOD_M2; return true; }
   if(tf == "M3")   { out_tf = PERIOD_M3; return true; }
   if(tf == "M4")   { out_tf = PERIOD_M4; return true; }
   if(tf == "M5")   { out_tf = PERIOD_M5; return true; }
   if(tf == "M6")   { out_tf = PERIOD_M6; return true; }
   if(tf == "M10")  { out_tf = PERIOD_M10; return true; }
   if(tf == "M12")  { out_tf = PERIOD_M12; return true; }
   if(tf == "M15")  { out_tf = PERIOD_M15; return true; }
   if(tf == "M20")  { out_tf = PERIOD_M20; return true; }
   if(tf == "M30")  { out_tf = PERIOD_M30; return true; }
   if(tf == "H1")   { out_tf = PERIOD_H1; return true; }
   if(tf == "H2")   { out_tf = PERIOD_H2; return true; }
   if(tf == "H3")   { out_tf = PERIOD_H3; return true; }
   if(tf == "H4")   { out_tf = PERIOD_H4; return true; }
   if(tf == "H6")   { out_tf = PERIOD_H6; return true; }
   if(tf == "H8")   { out_tf = PERIOD_H8; return true; }
   if(tf == "H12")  { out_tf = PERIOD_H12; return true; }
   if(tf == "D1")   { out_tf = PERIOD_D1; return true; }
   if(tf == "W1")   { out_tf = PERIOD_W1; return true; }
   if(tf == "MN1")  { out_tf = PERIOD_MN1; return true; }

   return false;
}

string BuildRatesResponse(const string jsonStr)
{
   string symbol = Symbol();
   string timeframe = "H1";
   double count_value = 200.0;
   string reason = "";
   string tmp = "";

   if(ExtractString(jsonStr, "symbol", tmp, reason))
      symbol = tmp;
   if(ExtractString(jsonStr, "timeframe", tmp, reason))
      timeframe = tmp;
   if(ExtractNumber(jsonStr, "count", count_value, reason))
      ;

   if(!SymbolSelect(symbol, true))
   {
      int err = GetLastError();
      ResetLastError();
      return StringFormat(
         "{\"error\":\"Symbol not found\",\"symbol\":\"%s\",\"last_error\":%d}",
         EscapeJson(symbol),
         err
      );
   }

   string timeframe_upper = timeframe;
   StringToUpper(timeframe_upper);
   ENUM_TIMEFRAMES tf_enum;
   if(!TimeframeFromString(timeframe_upper, tf_enum))
   {
      return StringFormat(
         "{\"error\":\"Unsupported timeframe\",\"timeframe\":\"%s\"}",
         EscapeJson(timeframe_upper)
      );
   }

   int count = (int)MathRound(count_value);
   if(count < 1) count = 1;
   if(count > 5000) count = 5000;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   ResetLastError();
   int copied = CopyRates(symbol, tf_enum, 0, count, rates);
   if(copied <= 0)
   {
      int err = GetLastError();
      ResetLastError();
      return StringFormat(
         "{\"error\":\"CopyRates failed\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"count\":%d,\"last_error\":%d}",
         EscapeJson(symbol),
         EscapeJson(timeframe_upper),
         count,
         err
      );
   }

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   string bars = "[";
   for(int i = copied - 1; i >= 0; i--)
   {
      string item = StringFormat(
         "{\"timestamp\":%d,\"open\":%s,\"high\":%s,\"low\":%s,\"close\":%s,\"tick_volume\":%I64d,\"spread\":%d,\"real_volume\":%I64d}",
         (int)rates[i].time,
         DoubleToString(rates[i].open, digits),
         DoubleToString(rates[i].high, digits),
         DoubleToString(rates[i].low, digits),
         DoubleToString(rates[i].close, digits),
         rates[i].tick_volume,
         rates[i].spread,
         rates[i].real_volume
      );
      bars += item;
      if(i > 0) bars += ",";
   }
   bars += "]";

   return StringFormat(
      "{\"action\":\"RATES\",\"symbol\":\"%s\",\"timeframe\":\"%s\",\"count\":%d,\"bars\":%s}",
      EscapeJson(symbol),
      EscapeJson(timeframe_upper),
      copied,
      bars
   );
}

string ExecuteTrade(const string jsonStr)
{
   string reason = "";
   string symbol = "";
   string direction = "";
   string order_type = "MARKET";
   double volume = 0.0;
   double entry_price = 0.0;
   double sl_pips = 0.0;
   double tp1_pips = 0.0;
   double tp2_pips = 0.0;
   string comment = "";
   ResetLastError();

   if(!ExtractString(jsonStr, "symbol", symbol, reason))
      return StringFormat("{\"error\":\"%s\"}", EscapeJson(reason));

   if(!ExtractString(jsonStr, "direction", direction, reason))
      return StringFormat("{\"error\":\"%s\"}", EscapeJson(reason));

   if(!ExtractString(jsonStr, "order_type", order_type, reason))
      order_type = "MARKET";

   if(!ExtractNumber(jsonStr, "volume", volume, reason))
      return StringFormat("{\"error\":\"%s\"}", EscapeJson(reason));

   string tmp_reason = "";
   if(!ExtractNumber(jsonStr, "entry_price", entry_price, tmp_reason)) entry_price = 0.0;
   if(!ExtractNumber(jsonStr, "stop_loss", sl_pips, tmp_reason)) sl_pips = 0.0;
   if(!ExtractNumber(jsonStr, "take_profit_1", tp1_pips, tmp_reason)) tp1_pips = 0.0;
   if(!ExtractNumber(jsonStr, "take_profit_2", tp2_pips, tmp_reason)) tp2_pips = 0.0;
   if(!ExtractString(jsonStr, "comment", comment, tmp_reason)) comment = "";

   if(volume <= 0.0)
      return "{\"error\":\"Invalid volume\"}";

   if(!SymbolSelect(symbol, true))
   {
      int err = GetLastError();
      ResetLastError();
      return StringFormat("{\"error\":\"Symbol not found\",\"last_error\":%d}", err);
   }

   string trade_reason = "";
   if(!TradingAllowed(trade_reason))
      return StringFormat("{\"error\":\"%s\"}", EscapeJson(trade_reason));

   StringToUpper(direction);
   StringToUpper(order_type);

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.symbol = symbol;
   request.volume = volume;
   request.deviation = 10;
   request.magic = MagicNumber;
   request.comment = comment;

   if(order_type == "LIMIT")
   {
      if(entry_price <= 0.0)
         return "{\"error\":\"Invalid entry_price for LIMIT order\"}";

      request.action = TRADE_ACTION_PENDING;
      request.price = entry_price;

      if(direction == "LONG" || direction == "BUY")
      {
         double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
         if(entry_price >= ask)
            return "{\"error\":\"BUY LIMIT price must be below current ask\"}";

         request.type = ORDER_TYPE_BUY_LIMIT;
         request.sl = (sl_pips > 0.0) ? request.price - sl_pips * Point() : 0.0;
         request.tp = (tp1_pips > 0.0) ? request.price + tp1_pips * Point() : 0.0;
      }
      else if(direction == "SHORT" || direction == "SELL")
      {
         double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
         if(entry_price <= bid)
            return "{\"error\":\"SELL LIMIT price must be above current bid\"}";

         request.type = ORDER_TYPE_SELL_LIMIT;
         request.sl = (sl_pips > 0.0) ? request.price + sl_pips * Point() : 0.0;
         request.tp = (tp1_pips > 0.0) ? request.price - tp1_pips * Point() : 0.0;
      }
      else
      {
         return "{\"error\":\"Invalid direction\"}";
      }
   }
   else if(order_type == "MARKET")
   {
      request.action = TRADE_ACTION_DEAL;
      if(direction == "LONG" || direction == "BUY")
      {
         request.type = ORDER_TYPE_BUY;
         request.price = SymbolInfoDouble(symbol, SYMBOL_ASK);
         request.sl = (sl_pips > 0.0) ? request.price - sl_pips * Point() : 0.0;
         request.tp = (tp1_pips > 0.0) ? request.price + tp1_pips * Point() : 0.0;
      }
      else if(direction == "SHORT" || direction == "SELL")
      {
         request.type = ORDER_TYPE_SELL;
         request.price = SymbolInfoDouble(symbol, SYMBOL_BID);
         request.sl = (sl_pips > 0.0) ? request.price + sl_pips * Point() : 0.0;
         request.tp = (tp1_pips > 0.0) ? request.price - tp1_pips * Point() : 0.0;
      }
      else
      {
         return "{\"error\":\"Invalid direction\"}";
      }
   }
   else
   {
      return "{\"error\":\"Invalid order_type\"}";
   }

   if(OrderSend(request, result))
   {
      return StringFormat(
         "{\"action\":\"EXECUTION\",\"ticket\":%d,\"price\":%.5f,\"volume\":%.2f}",
         (int)result.order,
         result.price,
         result.volume
      );
   }

   int err = GetLastError();
   string resp = StringFormat("{\"error\":\"OrderSend failed\",\"retcode\":%d,\"last_error\":%d}", result.retcode, err);
   ResetLastError();
   return resp;
}

string BuildResponse(const string jsonStr)
{
   string action = "";
   string reason = "";

   if(DebugLogging) PrintFormat("ZMQ RX: %s", jsonStr);

   if(!ExtractString(jsonStr, "action", action, reason))
   {
      if(DebugLogging) PrintFormat("ZMQ invalid: %s", reason);
      return StringFormat("{\"error\":\"Invalid JSON\",\"reason\":\"%s\"}", EscapeJson(reason));
   }

   StringToUpper(action);
   if(DebugLogging) PrintFormat("ZMQ action: %s", action);

   if(action == "PING")
      return "{\"action\":\"PONG\"}";

   if(action == "ACCOUNT")
      return BuildAccountResponse();

   if(action == "POSITIONS")
      return BuildPositionsResponse();

   if(action == "SYMBOLS")
      return BuildSymbolsResponse();

   if(action == "TRADE")
      return ExecuteTrade(jsonStr);

   if(action == "RATES")
      return BuildRatesResponse(jsonStr);

   return StringFormat("{\"error\":\"Unknown action\",\"action\":\"%s\"}", EscapeJson(action));
}

void HandleRequest(const string jsonStr)
{
   ResetLastError();
   string response = BuildResponse(jsonStr);
   int err = GetLastError();
   if(err != 0 && response == "")
   {
      if(DebugLogging) PrintFormat("BuildResponse error: %d", err);
      response = StringFormat("{\"error\":\"RuntimeError\",\"code\":%d}", err);
   }
   if(response == "") response = "{\"error\":\"Empty response\"}";

   if(!cmd_socket.send(response))
   {
      Print("ZMQ send failed");
   }
}

string MakeEndpoint(const string host, const int port)
{
   string h = host;
   if(StringLen(h) == 0) h = "*";
   return StringFormat("tcp://%s:%d", h, port);
}

void PublishLiveData()
{
   if(live_socket == NULL) return;
   MqlTick tick;
   if(SymbolInfoTick(Symbol(), tick))
   {
      string json = StringFormat(
         "{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f,\"time\":%d}",
         Symbol(),
         tick.bid,
         tick.ask,
         (int)TimeCurrent()
      );
      live_socket.send(json);
   }
}

// --- lifecycle ---
int OnInit()
{
   string cmd_addr  = MakeEndpoint(PythonHost, PythonPort);
   string data_addr = MakeEndpoint(PythonHost, DataPort);
   string live_addr = MakeEndpoint(PythonHost, LivePort);

   cmd_socket = new Socket(context, ZMQ_REP);
   if(!cmd_socket.bind(cmd_addr))
   {
      PrintFormat("Failed to bind cmd socket: %s", cmd_addr);
      return INIT_FAILED;
   }

   data_socket = new Socket(context, ZMQ_PUSH);
   if(!data_socket.bind(data_addr))
   {
      PrintFormat("Failed to bind data socket: %s", data_addr);
      return INIT_FAILED;
   }

   live_socket = new Socket(context, ZMQ_PUB);
   if(!live_socket.bind(live_addr))
   {
      PrintFormat("Failed to bind live socket: %s", live_addr);
      return INIT_FAILED;
   }

   PrintFormat("ZeroMQ Bridge started: cmd=%s data=%s live=%s", cmd_addr, data_addr, live_addr);
   EventSetMillisecondTimer(100);
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   if(cmd_socket == NULL) return;

   int processed = 0;
   while(processed < MaxMessagesPerTick)
   {
      ZmqMsg request;
      if(!cmd_socket.recv(request, true)) break;

      string msg = request.getData();
      HandleRequest(msg);
      processed++;
   }

   PublishLiveData();

   if(DebugLogging && processed >= MaxMessagesPerTick)
   {
      PrintFormat("MaxMessagesPerTick reached: %d", MaxMessagesPerTick);
   }
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(cmd_socket != NULL) delete cmd_socket;
   if(data_socket != NULL) delete data_socket;
   if(live_socket != NULL) delete live_socket;
}
