"""Synthetic examples only, intentionally separate from user-provided records."""
MOCK_BUNDLE = {
 'patterns':[{'id':'MOCK-PAT-01','name':'示例 · 街坊茶馆','hook_formula':'问候 + 反差','body_formula':'分享经历','cta_formula':'邀请交流','template_skeleton':'{{问候}}！今天你最想分享什么？','evidence_status':'testing'}],
 'cases':[{'id':'MOCK-CASE-01','pattern_id':'MOCK-PAT-01','author_handle':'demo_account','raw_text':'示例文案：早上好，今天一起聊聊创作。','views':1000,'replies':50,'author_followers':200,'why_it_worked':'仅用于演示，不代表实战结果。'}],
 'posts':[{'id':'MOCK-POST-01','pattern_id':'MOCK-PAT-01','ref_case_ids':['MOCK-CASE-01'],'content':'示例草稿：茶已泡好，欢迎来聊聊今天的创作。','status':'draft_variant'}],
 'tactics':[{'id':'MOCK-REP-01','tactic_name':'示例 · 熟客奉茶','trigger_condition':'熟悉的朋友再次留言','reply_logic':'回应对方近况 + 邀请继续交流','sample_replies':['欢迎回来，今天有什么新鲜事？🍵']}]
}
