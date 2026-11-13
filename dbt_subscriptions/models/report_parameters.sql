select cast('{{ var('knowledge_cutoff') }}' as timestamptz) as knowledge_cutoff,
cast('{{ var('period_start') }}' as date) as period_start, cast('{{ var('period_end') }}' as date) as period_end
