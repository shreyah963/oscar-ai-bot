# QueryPlanningTool User Prompt

This is the default user prompt from `QueryPlanningPromptTemplate.java`.
Pass as `query_planner_user_prompt` parameter on the QueryPlanningTool at agent registration.

## Variables

These are substituted at runtime by QueryPlanningTool:
- `${parameters.question}` - The natural language question (required)
- `${parameters.index_mapping:-}` - Index mapping JSON (auto-fetched by QPT)
- `${parameters.query_fields:-}` - Query fields if provided
- `${parameters.sample_document:-}` - Sample document from the index (auto-fetched by QPT)
- `${parameters.current_time:-}` - Current UTC time
- `${parameters.embedding_model_id:-}` - Embedding model ID for neural search

---

## DEFAULT USER PROMPT

Question: ${parameters.question}
Mapping: ${parameters.index_mapping:-}
Query Fields: ${parameters.query_fields:-}
Sample Document from index:${parameters.sample_document:-}
In UTC:${parameters.current_time:-} format: yyyy-MM-dd'T'HH:mm:ss'Z'
Embedding Model ID for Neural Search:${parameters.embedding_model_id:- not provided}
==== OUTPUT ====
GIVE THE OUTPUT PART ONLY IN YOUR RESPONSE (a single JSON object)
Output:
