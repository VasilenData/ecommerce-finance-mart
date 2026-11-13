SELECT CAST(month AS DATE) AS month FROM generate_series(
    (SELECT period_start FROM {{ ref('report_parameters') }}),
    (SELECT period_end - INTERVAL '1 month' FROM {{ ref('report_parameters') }}), INTERVAL '1 month'
) AS series(month)
