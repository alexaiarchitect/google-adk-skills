      # No auth step: the Gemini API key is passed as an environment variable.
      # It also authenticates the Vertex AI evaluation service, whose facade
      # reads GOOGLE_API_KEY before any project, so safety_v1,
      # response_evaluation_score and the multi_turn_* metrics do run here.
      # They are billed as a separate service and ignore judge_model_options.
      # What a key cannot do: Workload Identity, or the Vertex-hosted session,
      # memory and artifact services.
