import re
from difflib import SequenceMatcher

def match_titles(yt_title, local_filename):
    """強固型雙重權重模糊匹配演算法：完美解決 You 與 Calling You 互相搶標籤的 Bug"""
    # 1. 基礎清洗與標準化
    yt_clean = yt_title.strip().lower()
    # 移除副檔名，並允許以 | 或 - 切割檔名做多段比對
    fn_clean = local_filename.replace(".mp3", "").strip().lower()
    local_parts = [p.strip() for p in re.split(r'[|\-]', fn_clean) if p.strip()]
    if not local_parts:
        local_parts = [fn_clean]

    highest_score = 0

    for p in local_parts:
        # 💡 策略一：絕對完全相同匹配（給予最高霸權分 2.0）
        if p == yt_clean:
            return 2.0
            
        # 💡 策略二：計算 Gestalt 模式匹配相似度 (0.0 ~ 1.0)
        score = SequenceMatcher(None, p, yt_clean).ratio()
        
        # 💡 策略三：長度差距懲罰機制
        # 如果兩者字串長度差距過大 (例如 "you" 只有 3 個字，"calling you" 有 11 個字)，進行合理扣分
        len_diff = abs(len(p) - len(yt_clean))
        if len_diff > 0:
            max_len = max(len(p), len(yt_clean))
            penalty = (len_diff / max_len) * 0.2  # 最高扣 0.2 分
            score -= penalty

        # 💡 策略四：字首/字尾包含加分（但設有天花板，絕對不超過完全匹配）
        if p in yt_clean or yt_clean in p:
            score += 0.1
            
        if score > highest_score:
            highest_score = score

    return highest_score
