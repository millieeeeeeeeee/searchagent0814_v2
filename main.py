from pyngrok import ngrok, conf
import requests
import pandas as pd
from datetime import datetime
import pygsheets
import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs


from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
    FlexMessage, FlexContainer
)
from linebot.v3.messaging import MessagingApi, MessagingApiBlob, RichMenuRequest, RichMenuArea, RichMenuSize, RichMenuBounds, PostbackAction
from linebot.v3.webhooks import MessageEvent, TextMessageContent, PostbackEvent

"""gen-lang-client-0700041250-50b828903f03.json"""

"""##設置今日為2024-09-01"""


today = datetime(2024, 9, 1)

#上週
def get_last_week_range(today):
    start_of_this_week = today - timedelta(days=today.weekday())
    start = start_of_this_week - timedelta(days=7)
    end = start + timedelta(days=6)
    return start, end

#上月
def get_last_month_range(today):
    first_of_this_month = today.replace(day=1)
    last_day_of_last_month = first_of_this_month - timedelta(days=1)
    first_day_of_last_month = last_day_of_last_month.replace(day=1)
    return first_day_of_last_month, last_day_of_last_month

#str_date = start.strftime('%Y-%m-%d')       # '2024-08-19'
#iso_date = start.isoformat()           # 也是 '2024-08-19'
#print(str_date ,iso_date)

"""#資料處理"""

#Googlesheet Api
gc = pygsheets.authorize(service_account_file='./gen-lang-client-0700041250-50b828903f03.json')

survey_url = 'https://docs.google.com/spreadsheets/d/1QmpmeFcAqCEwW9lJUuEd40gD27SvlMoUSyzp7jvhG-E/edit?usp=sharing'
sh = gc.open_by_url(survey_url)

# 載入每張表
df1 = sh.worksheet_by_title('每日營業額').get_as_df(start='A1', index_colum=None, empty_value='', include_tailing_empty=False)
df2 = sh.worksheet_by_title('每日商品銷售量').get_as_df(start='A1', index_colum=None, empty_value='', include_tailing_empty=False)
df3 = sh.worksheet_by_title('目前庫存量').get_as_df(start='A1', index_colum=None, empty_value='', include_tailing_empty=False)

# 寬轉長格式
df1_long = df1.melt(id_vars="日期", var_name="分店", value_name="營業額")
df2_long = df2.melt(id_vars=["日期", "商品名稱"], var_name="分店", value_name="銷售量")

# 合併df1,df2為一張總表->merged
merged = df1_long.merge(df2_long, on=["日期", "分店"], how="left")
merged['日期'] = pd.to_datetime(merged['日期'], format='mixed')

#合併三張表資料
def merged_df():
  gc = pygsheets.authorize(service_account_file='./gen-lang-client-0700041250-50b828903f03.json')

  survey_url = 'https://docs.google.com/spreadsheets/d/1QmpmeFcAqCEwW9lJUuEd40gD27SvlMoUSyzp7jvhG-E/edit?usp=sharing'
  sh = gc.open_by_url(survey_url)

  # 載入每張表
  df1 = sh.worksheet_by_title('每日營業額').get_as_df(start='A1', index_colum=None, empty_value='', include_tailing_empty=False)
  df2 = sh.worksheet_by_title('每日商品銷售量').get_as_df(start='A1', index_colum=None, empty_value='', include_tailing_empty=False)
  df3 = sh.worksheet_by_title('目前庫存量').get_as_df(start='A1', index_colum=None, empty_value='', include_tailing_empty=False)

  # 寬轉長格式
  df1_long = df1.melt(id_vars="日期", var_name="分店", value_name="營業額")
  df2_long = df2.melt(id_vars=["日期", "商品名稱"], var_name="分店", value_name="銷售量")

  # 合併df1,df2為一張總表->merged
  merged = df1_long.merge(df2_long, on=["日期", "分店"], how="left")
  merged['日期'] = pd.to_datetime(merged['日期'], format='mixed')

  return merged

"""#Search Agent 20250811版"""

# GPT API 設定
GPT_API_KEY = "sk-tGDJdkhp45o5rnAmC021091179F1444d958d035a5dA8DfF5"
GPT_ENDPOINT = "https://free.v36.cm/v1/chat/completions"

GPT_headers = {
        "Authorization": f"Bearer {GPT_API_KEY}",
        "Content-Type": "application/json"
    }

def PhaseI_Parser_gpt(question):
  prompt = f"""
  你是一個資料查詢問題剖析器，目的是將一段自然語言問題，解析成結構化的 JSON 格式，用於後續資料處理。
  請針對每個問題，萃取下列欄位：

  目前系統時間設定為 2024/09/01，資料涵蓋時間為 2024/07/01～2024/08/31，請依此判斷時間。

  - `question_type`: 問題的類型，可為：
    - `"查詢"`：查找具體資料，例如「某店的營業額」、「某商品的銷售量」
    - `"統計"`：需整合多筆資料後回答，例如「總銷售量」、「平均營業額」
    - `"比較"`：兩組以上資料的比較，例如「A店比B店多幾份」、「這週比上週成長」
    - `"建議"`：請求系統提出建議，例如「要下架哪些品項」、「推薦熱銷商品」
    - `"篩選"`：需要根據條件篩出資料，例如「找出銷售為0的商品」、「哪些店庫存過剩」

  - `target_metric`: 使用者最關心的核心指標，例如：「營業額」「銷售量」「庫存量」「成長率」「是否該下架」

  - `filters`: 限制條件，**可有零或多個物件**包含：
    - 日期 ：以「%Y/%M/%D」形式呈現。若問題中未明確提及時間日期，請設為空物件。
    - 分店
    - 商品名稱
    - 其他（如「庫存過剩」「銷售為0」「熱賣」「飲品」））
    - 若無篩選條件請設為空物件

  - `required_tables`: 需要用到哪幾張表：
    - `"每日營業額"`
    - `"每日商品銷售量"`
    - `"目前庫存量"`

  - `chunk_strategy`: 根據問題重點選擇 chunk 方式：
    - `"chunk_day"`：以「日期」為主，例如比較週成長、時間趨勢
    - `"chunk_branch"`：以「分店」為主，例如比較店別績效
    - `"chunk_merchdise"`：以「商品」為主，例如找熱銷、滯銷、建議下架商品

  ---
  請輸出 **乾淨的 JSON 格式**，不要加上說明或註解。
  以下是使用者問題：{question}
  """
  payload = {
      "model": "gpt-3.5-turbo",#gpt-3.5-turbo
      "messages": [
          {"role": "user", "content": prompt}
      ]
  }
  response = requests.post(GPT_ENDPOINT, headers=GPT_headers, json=payload)
  response.raise_for_status()
  result = response.json()
  parsed_dict = eval(result["choices"][0]["message"]["content"])
  return parsed_dict

def chunk_revenue(parsed_dict,df):
  filters = parsed_dict.get("filters", {})
  date = filters.get("日期")
  branch = filters.get("分店")
  merchandise = filters.get("商品名稱")

  if date and "日期" in df.columns:
    df = df[df["日期"] == date]
  if branch :
    df = df.melt(id_vars=["日期"],var_name="分店",value_name="營業額")
    df = df[df["分店"] == branch]
  df['日期'] = pd.to_datetime(df['日期'], format='mixed').dt.date
  lines = ["《每日營業額》"]
  for _, row in df.iterrows():
      line = f"- 分店：{row['分店']}｜日期：{row['日期']}｜營業額：{row['營業額']}"
      lines.append(line)
  return "\n".join(lines)

def chunk_product(parsed_dict,df):
  filters = parsed_dict.get("filters", {})
  date = filters.get("日期")
  branch = filters.get("分店")
  merchandise = filters.get("商品名稱")

  if date and "日期" in df.columns:
    df = df[df["日期"] == date]
  if merchandise and "商品名稱" in df.columns:
    df = df[df["商品名稱"] == merchandise]
  if branch :
    df = df.melt(id_vars=["日期", "商品名稱"],var_name="分店",value_name="銷售量")
    df = df[df["分店"] == branch]
  df['日期'] = pd.to_datetime(df['日期'], format='mixed').dt.date
  lines = ["《每日商品銷售量》"]
  for _, row in df.iterrows():
      line = f"- 分店：{row['分店']}｜日期：{row['日期']}｜商品名稱：{row['商品名稱']}｜銷售量：{row['銷售量']}"
      lines.append(line)
  return "\n".join(lines)


def chunk_stock(parsed_dict,df):
  filters = parsed_dict.get("filters", {})
  date = filters.get("日期")
  branch = filters.get("分店")
  merchandise = filters.get("商品名稱")

  df = df.melt(id_vars=["商品名稱"],var_name="分店",value_name="庫存量")
  df.insert(0, "日期", today.strftime('%Y-%m-%d'))
  if merchandise:
    df = df[df["商品名稱"] == merchandise]
  if branch:
    df = df[df["分店"] == branch]

  lines = ["《每日商品銷售量》"]
  for _, row in df.iterrows():
      line = f"- 分店：{row['分店']}｜日期：{row['日期']}｜商品名稱：{row['商品名稱']}｜庫存量：{row['庫存量']}"
      lines.append(line)
  return "\n".join(lines)

def PhaseII_DataSelector(parsed_dict):
  required_tables = parsed_dict.get("required_tables", [])
  gc = pygsheets.authorize(service_account_file='./gen-lang-client-0700041250-50b828903f03.json')

  survey_url = 'https://docs.google.com/spreadsheets/d/1QmpmeFcAqCEwW9lJUuEd40gD27SvlMoUSyzp7jvhG-E/edit?usp=sharing'
  sh = gc.open_by_url(survey_url)

  data = []
  for table_name in required_tables:
    df = sh.worksheet_by_title(table_name).get_as_df(start='A1', index_colum=None, empty_value='', include_tailing_empty=False)
    if table_name=='每日營業額':
      line=chunk_revenue(parsed_dict,df)
    elif table_name=='每日商品銷售量':
      line=chunk_product(parsed_dict,df)
    elif table_name=='目前庫存量':
      line=chunk_stock(parsed_dict,df)
    data.append(line)
  return "\n\n".join(data)


def PhaseIII_Answer_gpt(question,data,parsed_dict):
  prompt = f"""
      你是一位資料分析師，幫助使用者查詢便當店銷售資料。
      請根據以下便當店每日營業數據與商品銷售/庫存記錄:{data}
      使用者問題如下：
      「{question}」
      可以參考問題分析後的格式:
      {parsed_dict}

      用簡潔的話完整回答我問題
      """
  payload = {
      "model": "gpt-3.5-turbo",
      "messages": [
          {"role": "user", "content": prompt}
      ]
  }
  response = requests.post(GPT_ENDPOINT, headers=GPT_headers, json=payload)
  result = response.json()
  return result["choices"][0]["message"]["content"]

#整合 PhaseI~III
def final_gpt(question):
  parsed_dict=PhaseI_Parser_gpt(question)
  print(f"PhaseI_Parser_gpt 完成 {parsed_dict}")
  data=PhaseII_DataSelector(parsed_dict)
  print(f"PhaseII_DataSelector 完成 {data}")
  answer=PhaseIII_Answer_gpt(question,data,parsed_dict)
  return answer

"""#Flex Message模組

##分店查詢
"""

def UL_get_branch_selector():
    branches = ["台北中山店", "台中西屯店", "台南中西區店", "全部"]

    buttons = []
    for branch in branches:
        buttons.append({
            "type": "button",
            "action": {
                "type": "postback",
                "label": branch,
                "data": f"session=UL&step=select_date&branch={branch}"
            }
        })

    flex_content = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#A1C7E0",
            "contents": [
                {
                    "type": "text",
                    "text": "📍 請選擇分店",
                    "align": "center",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#000000"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": buttons
        }
    }

    flex_json_str = json.dumps(flex_content)
    flex_container = FlexContainer.from_json(flex_json_str)

    return FlexMessage(
        alt_text="請選擇分店",
        contents=flex_container
    )

def UL_get_date_selector(branch):
    flex_content = {
      "type": "bubble",
      "header": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#A1C7E0",
        "contents": [
          {
            "type": "text",
            "text": f"{branch}",
            "align": "center",
            "weight": "bold",
            "size": "lg",
            "color": "#000000"
          }
        ]
      },
      "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {
            "type": "button",
            "action": {
              "type": "postback",
              "label": "上週",
              "data": f"session=UL&step=last_week_show_result&branch={branch}"
            }
          },
          {
            "type": "button",
            "action": {
              "type": "postback",
              "label": "上月",
              "data": f"session=UL&step=last_month_show_result&branch={branch}"
            }
          },
          {
            "type": "button",
            "action": {
              "type": "datetimepicker",
              "label": "選擇日期",
              "data": f"session=UL&step=one_day_show_result&branch={branch}",
              "mode": "date",
              "max": "2024-08-31",
              "min": "2024-07-01"
            }
          }
        ]
      }
    }

    flex_json_str = json.dumps(flex_content)
    flex_container = FlexContainer.from_json(flex_json_str)

    return FlexMessage(
        alt_text="請選擇日期",
        contents=flex_container
    )

def UL_query_branch_data(branch, date):
    return f"✅ 成功查詢！\n分店：{branch}\n日期：{date}"

def UL_days_detail_list(branch, start, end):
    df = merged_df()
    matched = df[(df["日期"] >= start) & (df["日期"] <= end)]

    if branch == "全部":
        filtered = matched.copy()
    else:
        filtered = matched[matched["分店"] == branch]

    # 銷售量總和（依商品）
    original_order = filtered["商品名稱"].drop_duplicates().tolist()
    sales_summary = filtered.groupby("商品名稱", as_index=False)["銷售量"].sum()
    sales_summary = sales_summary.set_index("商品名稱").loc[original_order].reset_index()

    # 營業額總和
    if branch == "全部":
        revenue_df = filtered.drop_duplicates(subset=["分店", "日期"])
        revenue = revenue_df["營業額"].sum()
    else:
        revenue = filtered["營業額"].sum()

    # 商品資料列（每列一個 Box）
    product_boxes = []
    for _, row in sales_summary.iterrows():
        product_boxes.append({
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": row["商品名稱"], "color": "#aaaaaa", "size": "sm", "align": "start"},
                {"type": "text", "text": str(row["銷售量"]), "color": "#666666", "size": "sm", "align": "center"},
            ]
        })

    # 日期文字顯示範圍
    start=start.strftime('%Y-%m-%d')
    end=end.strftime('%Y-%m-%d')
    date_range = f"{start} ~ {end}"

    # Bubble 結構
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": branch,
                    "weight": "bold",
                    "size": "lg",
                    "align": "center",
                    "flex": 2
                }
            ],
            "margin": "lg",
            "spacing": "lg",
            "backgroundColor": "#4F9D9D"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "box",
                    "layout": "baseline",
                    "contents": [
                        {
                            "type": "text",
                            "text": date_range,
                            "weight": "bold",
                            "size": "lg",
                            "align": "start"
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "baseline",
                    "contents": [
                        {"type": "text", "text": "營業額", "size": "md"},
                        {"type": "text", "text": f"${revenue:,}", "align": "start"}
                    ],
                    "margin": "lg"
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "xs",
                    "spacing": "sm",
                    "contents": [
                        {"type": "separator"},
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "產品", "align": "start"},
                                {"type": "text", "text": "銷售量", "align": "center"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": product_boxes
                        }
                    ]
                }
            ]
        }
    }

    return FlexMessage(
        alt_text=f"{branch} {date_range} 銷售報告",
        contents=FlexContainer.from_json(json.dumps(bubble))
    )

def UL_one_day_detail_list(branch, date):
    if branch == "全部":
      df=merged_df()
      matched=df[df["日期"] == date]
      original_order = matched["商品名稱"].drop_duplicates().tolist()
      matched = matched.groupby("商品名稱", as_index=False)["銷售量"].sum()
      matched = matched.set_index("商品名稱").loc[original_order].reset_index()

      revenue_df=df[df["日期"] == date].drop_duplicates(subset=["分店"])
      revenue = revenue_df["營業額"].sum()
    else:
      df=merged_df()
      matched = df[(df["分店"] == branch) & (df["日期"] == date)]
      revenue = matched["營業額"].iloc[0]

    # 商品資料列（每列一個 Box）
    product_boxes = []
    for _, row in matched.iterrows():
        product_boxes.append({
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": row["商品名稱"], "color": "#aaaaaa", "size": "sm", "align": "start"},
                {"type": "text", "text": str(row["銷售量"]), "color": "#666666", "size": "sm", "align": "center"},
            ]
        })

    # Bubble 結構
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": branch,
                    "weight": "bold",
                    "size": "lg",
                    "align": "center",
                    "flex": 2
                }
            ],
            "margin": "lg",
            "spacing": "lg",
            "backgroundColor": "#4F9D9D"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "box",
                    "layout": "baseline",
                    "contents": [
                        {
                            "type": "text",
                            "text": date,
                            "weight": "bold",
                            "size": "lg",
                            "align": "start"
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "baseline",
                    "contents": [
                        {"type": "text", "text": "營業額", "size": "md"},
                        {"type": "text", "text": f"${revenue:,}", "align": "start"}
                    ],
                    "margin": "lg"
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "xs",
                    "spacing": "sm",
                    "contents": [
                        {"type": "separator"},
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "產品", "align": "start"},
                                {"type": "text", "text": "銷售量", "align": "center"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": product_boxes
                        }
                    ]
                }
            ]
        }
    }

    return FlexMessage(
        alt_text=f"{branch} {date} 銷售報告",
        contents=FlexContainer.from_json(json.dumps(bubble))
    )

"""##日期查詢"""

def get_date_selector():
    flex_content = {
        "type": "bubble",
        "header": {
          "type": "box",
          "layout": "vertical",
          "backgroundColor": "#A1C7E0",
          "contents": [
            {
              "type": "text",
              "text": "日期查詢",
              "align": "center",
              "weight": "bold",
              "size": "lg",
              "color": "#000000"
            }
          ]
        },
        "body": {
          "type": "box",
          "layout": "vertical",
          "contents": [
            {
              "type": "button",
              "action": {
                "type": "datetimepicker",
                "label": "選擇日期",
                "data": "session=UM&step=show_result",
                "mode": "date",
                "max": "2024-08-31",
                "min": "2024-07-01"
              }
            }
          ]
        }
      }

    flex_json_str = json.dumps(flex_content)
    flex_container = FlexContainer.from_json(flex_json_str)

    return FlexMessage(
        alt_text="請選擇日期",
        contents=flex_container
    )

def UM_get_date_selector():
    flex_content = {
      "type": "bubble",
      "header": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#A1C7E0",
        "contents": [
          {
            "type": "text",
            "text": "日期查詢",
            "align": "center",
            "weight": "bold",
            "size": "lg",
            "color": "#000000"
          }
        ]
      },
      "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {
            "type": "button",
            "action": {
              "type": "postback",
              "label": "上週",
              "data": "session=UM&step=last_week_show_result"
            }
          },
          {
            "type": "button",
            "action": {
              "type": "postback",
              "label": "上月",
              "data": "session=UM&step=last_month_show_result"
            }
          },
          {
            "type": "button",
            "action": {
              "type": "datetimepicker",
              "label": "選擇日期",
              "data": "session=UM&step=one_day_show_result",
              "mode": "date",
              "max": "2024-08-31",
              "min": "2024-07-01"
            }
          }
        ]
      }
    }

    flex_json_str = json.dumps(flex_content)
    flex_container = FlexContainer.from_json(flex_json_str)

    return FlexMessage(
        alt_text="請選擇日期",
        contents=flex_container
    )

def UM_days_detail_list(start, end):
    df=merged_df()
    matched = df[["日期", "分店", "營業額"]]
    matched = matched[(matched["日期"] >= start) & (matched["日期"] <= end)]
    normal_rows = matched.drop_duplicates()
    original_order = normal_rows["分店"].drop_duplicates().tolist()
    normal_rows = normal_rows.groupby("分店", as_index=False)["營業額"].sum()
    normal_rows = normal_rows.set_index("分店").loc[original_order].reset_index()
    hq_row = pd.DataFrame([{
        "分店": "總計",
        "營業額": normal_rows["營業額"].sum()
        }])

    # 日期文字顯示範圍
    start=start.strftime('%Y-%m-%d')
    end=end.strftime('%Y-%m-%d')
    date_range = f"{start} ~ {end}"

    # 第一行：表頭
    contents = [{
        "type": "box",
        "layout": "baseline",
        "contents": [
            {"type": "text", "text": "分店", "align": "center", "color": "#aaaaaa"},
            {"type": "text", "text": "營業額", "align": "center", "color": "#aaaaaa"}
        ]
    }]

    # 加入各分店資料
    for _, row in normal_rows.iterrows():
        contents.append({
            "type": "box",
            "layout": "baseline",
            "contents": [
                {"type": "text", "text": row["分店"], "align": "center"},
                {"type": "text", "text": f"${(row['營業額']):,}", "align": "center"}
            ],
            "margin": "sm"
        })

    # 加入分隔線 + 總部資料
    if not hq_row.empty:
        hq = hq_row.iloc[0]
        contents.append({
            "type": "separator",
            "color": "#000000",
            "margin": "md"
        })
        contents.append({
            "type": "box",
            "layout": "baseline",
            "contents": [
                {"type": "text", "text": hq["分店"], "align": "center"},
                {"type": "text", "text": f"${(hq['營業額']):,}", "align": "center"}
            ],
            "margin": "md"
        })

    # 組合 Flex Bubble
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#4F9D9D",
            "contents": [
                {
                    "type": "text",
                    "text": date_range,
                    "align": "center",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#000000"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents
        }
    }

    # 轉為 FlexMessage
    flex_json_str = json.dumps(bubble, ensure_ascii=False)
    flex_container = FlexContainer.from_json(flex_json_str)

    return FlexMessage(
        alt_text=f"{date_range} 各分店營業額",
        contents=flex_container
    )

def UM_one_day_detail_list(date):
    df=merged_df()
    matched = df[["日期", "分店", "營業額"]]
    normal_rows = matched[matched["日期"] == date].drop_duplicates(subset=["分店"])
    revenue=matched["營業額"].sum()
    hq_row = pd.DataFrame([{
        "分店": "總計",
        "營業額": normal_rows["營業額"].sum()
        }])

    # 第一行：表頭
    contents = [{
        "type": "box",
        "layout": "baseline",
        "contents": [
            {"type": "text", "text": "分店", "align": "center", "color": "#aaaaaa"},
            {"type": "text", "text": "營業額", "align": "center", "color": "#aaaaaa"}
        ]
    }]

    # 加入各分店資料
    for _, row in normal_rows.iterrows():
        contents.append({
            "type": "box",
            "layout": "baseline",
            "contents": [
                {"type": "text", "text": row["分店"], "align": "center"},
                {"type": "text", "text": f"${(row['營業額']):,}", "align": "center"}
            ],
            "margin": "sm"
        })

    # 加入分隔線 + 總部資料
    if not hq_row.empty:
        hq = hq_row.iloc[0]
        contents.append({
            "type": "separator",
            "color": "#000000",
            "margin": "md"
        })
        contents.append({
            "type": "box",
            "layout": "baseline",
            "contents": [
                {"type": "text", "text": hq["分店"], "align": "center"},
                {"type": "text", "text": f"${(hq['營業額']):,}", "align": "center"}
            ],
            "margin": "md"
        })

    # 組合 Flex Bubble
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#4F9D9D",
            "contents": [
                {
                    "type": "text",
                    "text": date,
                    "align": "center",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#000000"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents
        }
    }

    # 轉為 FlexMessage
    flex_json_str = json.dumps(bubble, ensure_ascii=False)
    flex_container = FlexContainer.from_json(flex_json_str)

    return FlexMessage(
        alt_text=f"{date} 各分店營業額",
        contents=flex_container
    )

"""##庫存查詢"""

def UR_get_branch_selector():
    branches = ["台北中山店", "台中西屯店", "台南中西區店"]

    buttons = []
    for branch in branches:
        buttons.append({
            "type": "button",
            "action": {
                "type": "postback",
                "label": branch,
                "data": f"session=UR&step=show_result&branch={branch}"
            }
        })

    flex_content = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#A1C7E0",
            "contents": [
                {
                    "type": "text",
                    "text": "📍 請選擇分店",
                    "align": "center",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#000000"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": buttons
        }
    }

    flex_json_str = json.dumps(flex_content)
    flex_container = FlexContainer.from_json(flex_json_str)

    return FlexMessage(
        alt_text="請選擇分店",
        contents=flex_container
    )

def UR_detail_list(branch):
    merged=df3[["商品名稱",branch,"總部"]]

    #目前假設為2024-09-01，之後可更換
    #today = datetime.now().strftime("%Y-%m-%d")
    today = datetime(2024, 9, 1).strftime("%Y-%m-%d")

    # Header row
    contents = [
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "商品", "weight": "bold", "flex": 3, "size": "sm"},
                {"type": "text", "text": branch, "weight": "bold", "flex": 3, "size": "sm", "align": "center"},
                {"type": "text", "text": "總部", "weight": "bold", "flex": 2, "size": "sm", "align": "center"},
            ]
        }
    ]

    # 資料列
    for _, row in merged.iterrows():
        contents.append({
            "type": "box",
            "layout": "baseline",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": str(row["商品名稱"]), "flex": 3, "size": "sm"},
                {"type": "text", "text": str(row[branch]), "flex": 3, "size": "sm", "align": "center"},
                {"type": "text", "text": str(row["總部"]), "flex": 2, "size": "sm", "align": "center"},
            ]
        })

    # 建立 bubble 結構
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#4F9D9D",
            "contents": [
                {"type": "text", "text": f"{today}｜商品庫存", "weight": "bold", "color": "#ffffff", "align": "center"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": contents
        }
    }

    # 封裝成 Flex Message
    flex_json_str = json.dumps(bubble)
    flex_container = FlexContainer.from_json(flex_json_str)

    return FlexMessage(
        alt_text=f"{today}｜{branch} 商品庫存",
        contents=flex_container
    )

"""#RichMenu 模組"""



def create_richmenu_for_six():
    # 建立 Rich Menu
    create_rich_menu_request = RichMenuRequest(
      size=RichMenuSize(width=2500, height=1686),
      selected=True,
      name="main_menu",
      chat_bar_text="主選單",
      areas=[
          RichMenuArea(bounds=RichMenuBounds(x=0, y=0, width=833, height=843),
                      action=PostbackAction(label="分店查詢", data="session=UL&step=select_branch")),
          RichMenuArea(bounds=RichMenuBounds(x=834, y=0, width=833, height=843),
                      action=PostbackAction(label="日期查詢", data="session=UM&step=select_date")),
          RichMenuArea(bounds=RichMenuBounds(x=1667, y=0, width=833, height=843),
                      action=PostbackAction(label="庫存查詢", data="session=UR&step=select_branch")),
          RichMenuArea(bounds=RichMenuBounds(x=0, y=843, width=833, height=843),
                      action=PostbackAction(label="銷售排行", data="session=LL&step=select_time")),
          RichMenuArea(bounds=RichMenuBounds(x=834, y=843, width=833, height=843),
                      action=PostbackAction(label="功能2", data="session=LM&step=booking")),
          RichMenuArea(bounds=RichMenuBounds(x=1667, y=843, width=833, height=843),
                      action=PostbackAction(label="功能3", data="session=LR&step=service")),
      ]
    )


    # 建立 Rich Menu
    response = messaging_api.create_rich_menu(create_rich_menu_request)
    rich_menu_id = response.rich_menu_id
    print("✅ 六格Rich Menu 已建立，ID:", rich_menu_id)

    # 上傳圖片
    with open("./richmenu_background_six.png", "rb") as image:
      blob_api.set_rich_menu_image(rich_menu_id,
        body=bytearray(image.read()),
        _headers={'Content-Type': 'image/png'})
    print("✅ 圖片已上傳")

    # 設為預設 Rich Menu
    messaging_api.set_default_rich_menu(rich_menu_id)
    print("✅ 已設為預設 Rich Menu")

def create_richmenu_for_three():
    # 建立 Rich Menu
    create_rich_menu_request = RichMenuRequest(
      size=RichMenuSize(width=2500, height=843),
      selected=True,
      name="main_menu",
      chat_bar_text="主選單",
      areas=[
          RichMenuArea(bounds=RichMenuBounds(x=0, y=200, width=833, height=421),
                      action=PostbackAction(label="分店查詢", data="session=UL&step=select_branch")),
          RichMenuArea(bounds=RichMenuBounds(x=834, y=200, width=833, height=421),
                      action=PostbackAction(label="日期查詢", data="session=UM&step=select_date")),
          RichMenuArea(bounds=RichMenuBounds(x=1667, y=200, width=833, height=421),
                      action=PostbackAction(label="庫存查詢", data="session=UR&step=select_branch"))
      ]
    )


    # 建立 Rich Menu
    response = messaging_api.create_rich_menu(create_rich_menu_request)
    rich_menu_id = response.rich_menu_id
    print("✅ 三格Rich Menu 已建立，ID:", rich_menu_id)

    # 上傳圖片
    with open("./richmenu_background_three.png", "rb") as image:
      blob_api.set_rich_menu_image(rich_menu_id,
        body=bytearray(image.read()),
        _headers={'Content-Type': 'image/png'})
    print("✅ 圖片已上傳")

    # 設為預設 Rich Menu
    messaging_api.set_default_rich_menu(rich_menu_id)
    print("✅ 已設為預設 Rich Menu")

"""##RichMenu 功能

選擇功能
"""

def handle_richmenu_session(event, data_dict):
    session = data_dict.get("session")

    if session == "UL":
        return search_from_branch(event, data_dict)
    elif session == "UM":
        return search_from_date(event, data_dict)
    elif session == "UR":
        return search_inventory(event, data_dict)
    else:
        return TextMessage(text=f"⚠️ 尚未實作的功能區塊 session={session}")

"""UL左上"""

def search_from_branch(event, data_dict):
    step = data_dict.get("step")


    if step == "select_branch":
        return UL_get_branch_selector()

    elif step == "select_date":
        branch = data_dict.get("branch", "")
        return UL_get_date_selector(branch)

    elif step == "one_day_show_result":
      branch = data_dict.get("branch", "")
      date = event.postback.params.get("date")
      return UL_one_day_detail_list(branch, date)
    elif step == "last_week_show_result":
      branch = data_dict.get("branch", "")
      start, end = get_last_week_range(today)
      return UL_days_detail_list(branch, start, end)
    elif step == "last_month_show_result":
      branch = data_dict.get("branch", "")
      start, end = get_last_month_range(today)
      return UL_days_detail_list(branch, start, end)

    else:
      return TextMessage(text=f"⚠️ UL無法辨識步驟：{step}")

"""UM中上"""

def search_from_date(event, data_dict):
    step = data_dict.get("step")

    if step == "select_date":
        return UM_get_date_selector()

    elif step == "one_day_show_result":
        date = event.postback.params.get("date")
        return UM_one_day_detail_list(date)

    elif step == "last_week_show_result":
        start, end = get_last_week_range(today)
        return UM_days_detail_list(start, end)

    elif step == "last_month_show_result":
        start, end = get_last_month_range(today)
        return UM_days_detail_list(start, end)

    else:
        return TextMessage(text=f"⚠️ UM無法辨識步驟：{step}")

"""UR右上"""

def search_inventory(event, data_dict):
    step = data_dict.get("step")

    if step == "select_branch":
        return UR_get_branch_selector()
    elif step == "show_result":
        branch = data_dict.get("branch", "")
        return UR_detail_list(branch)

    else:
        return TextMessage(text=f"⚠️ UR無法辨識步驟：{step}")

"""#LineBot + SearchAgent + FlexMessage"""


#LINE Channel Setting (LINE Developers)
access_token = '6Ety0+qdlm9GdM/VlFX5K+lnQu5IMYBWeRba2FUpmzB0TwQIfoNYA6tn/m2dnyNR/1sIiO8ek4gmrJXm4J5P6Th3Fhpz6cdAtHQwdhsk/ibMiApjxanoKghogEmdwTo7sl6fjm3FRYkJAxKpL1PKqgdB04t89/1O/w1cDnyilFU='
secret = 'b4d6920e3dcfd210051f1b413fdf894c'

#20250804_test
# 初始化 Flask
app = Flask(__name__)

# 初始化 Messaging API 和 handler
configuration = Configuration(access_token=access_token)
api_client = ApiClient(configuration)
messaging_api = MessagingApi(api_client)
handler = WebhookHandler(secret)
blob_api = MessagingApiBlob(api_client)



create_richmenu_for_three()

# 📬 Webhook 接收路由
@app.route("/", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Webhook 處理錯誤：", e)
        abort(400)

    return "OK"

# 🟦 處理文字訊息
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    question = event.message.text
    reply = final_gpt(question)
    messaging_api.reply_message(
      ReplyMessageRequest(
          reply_token=event.reply_token,
          messages=[TextMessage(text=str(reply))]
              )
    )

# 🟨 處理 Postback（例如 datetimepicker 或按鈕）
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    print(f"收到 postback：{data}")

    data_dict = {k: v[0] for k, v in parse_qs(data).items()}

    try:
        reply = handle_richmenu_session(event, data_dict)
    except Exception as e:
        reply = TextMessage(text=f"❌ 發生錯誤：{e}")

    messaging_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[reply]
        )
    )

if __name__ == "__main__":
    app.run()