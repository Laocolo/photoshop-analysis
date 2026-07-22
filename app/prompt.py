"""AI 点评 prompt 模板（结构化输出）。"""

SYSTEM_PROMPT = (
    "你是一位资深摄影老师，正在一对一教一名初学者。"
    "点评要具体、直接、说人话：指出画面里具体位置的具体优点或问题，"
    "不要空泛的套话（如“构图不错，继续努力”），也不要堆砌术语。"
    "所有内容用中文回答。"
)

_FIELD_LABELS = (
    ("aperture", "光圈"),
    ("shutter", "快门"),
    ("iso", "ISO"),
    ("focal_length", "焦距"),
    ("datetime", "拍摄时间"),
    ("camera", "相机"),
)

_OUTPUT_SPEC = """请严格按以下结构输出（markdown）：

## 总评
给出评级：**好图** / **普通** / **较差**（三选一），10 分制打分（如 6.5/10），再用一句话总结。

## 亮点
这张照片好在哪里：用了什么构图框架（三分法、引导线、框架式、对称、留白、对角线等，说明主体在画面什么位置）、景别（远景/全景/中景/近景/特写）、用光、色彩、瞬间抓拍。没有明显亮点就直说。

## 不足
差在哪里：构图、曝光、对焦、背景杂乱、地平线歪斜、主体不突出等，按严重程度逐条列出，说明在画面哪个位置。

## 参数分析
结合给出的拍摄参数，分析光圈 / 快门 / ISO / 焦距的搭配对该场景是否合理、可以怎么调。如果没有参数，跳过本节。

## 后期调整建议
如果通过后期拯救或提升这张照片：按调整顺序给出具体步骤（二次构图裁剪 → 曝光/对比度 → 白平衡 → 高光/阴影/黑白场 → 色彩/HSL → 锐化/降噪），每项说明调整方向与大致幅度（以 Lightroom / Adobe Camera Raw 的调整项为参照），并指出该步解决的是"不足"里的哪个问题。前期无法靠后期弥补的问题（如严重跑焦、死黑死白）直说，不要假装能救。

## 改进建议
给出 2-3 条下次拍摄可以直接照做的具体建议。"""


def build_user_prompt(params: dict, extra_time: str = "", intent: str = "") -> str:
    """组装 user prompt：EXIF 参数 + 用户补充 + 输出结构要求。"""
    lines = ["请点评我拍的这张照片，我正在学习摄影。"]

    given = [(label, params[key]) for key, label in _FIELD_LABELS if params.get(key)]
    if given:
        lines.append("\n拍摄参数：")
        lines.extend(f"- {label}：{value}" for label, value in given)
    else:
        lines.append("\n（这张照片没有提供拍摄参数，请主要依据画面本身点评。）")

    if extra_time.strip():
        lines.append(f"\n大概拍摄时间/光线：{extra_time.strip()}")
    if intent.strip():
        lines.append(f"我想要的拍摄效果：{intent.strip()}")

    lines.append("\n" + _OUTPUT_SPEC)
    return "\n".join(lines)
