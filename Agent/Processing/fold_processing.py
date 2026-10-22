"""分别记录存储类型、统计特征和有来源的测量语义，不改变输入数据。"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd


PROFILE_THRESHOLDS = {
    'low_cardinality_max_distinct': 10,
    'low_frequency_max_count': 5,
    'low_frequency_max_ratio': 0.01,
    'high_missing_ratio': 0.3,
}


def get_data_summary(df: pd.DataFrame) -> dict[str, Any]:
    """统计提示不决定业务含义；整数、低基数和低频可以同时存在。"""
    profiles: dict[str, dict[str, Any]] = {}
    for column in df.columns:
        series = df[column]
        present = series.dropna()
        counts = present.value_counts()
        numeric = bool(pd.api.types.is_numeric_dtype(series.dtype) and not pd.api.types.is_bool_dtype(series.dtype))
        finite = present[np.isfinite(present.to_numpy(dtype=float))] if numeric else present
        integer = bool(numeric and len(finite) and (finite % 1 == 0).all())
        nonnegative = bool(numeric and len(finite) and (finite >= 0).all())
        rare = counts[(counts <= PROFILE_THRESHOLDS['low_frequency_max_count']) |
                      (counts / max(len(present), 1) <= PROFILE_THRESHOLDS['low_frequency_max_ratio'])]
        features = {
            'is_numeric': numeric, 'integer_valued': integer if numeric else None,
            'nonnegative': nonnegative if numeric else None,
            'distinct_count': int(len(counts)), 'distinct_ratio': len(counts) / max(len(present), 1),
            'low_cardinality': bool(len(counts) and len(counts) <= PROFILE_THRESHOLDS['low_cardinality_max_distinct']),
            'low_frequency': bool(len(rare)), 'low_frequency_value_count': int(len(rare)),
            'non_finite_count': int(len(present) - len(finite)) if numeric else 0,
        }
        missing = int(series.isna().sum())
        inferred = 'empty' if present.empty else 'numeric' if numeric else 'datetime' if pd.api.types.is_datetime64_any_dtype(series.dtype) else 'categorical'
        issues = []
        if missing / max(len(series), 1) > PROFILE_THRESHOLDS['high_missing_ratio']:
            issues.append('缺失率超过30%')
        if len(counts) <= 1:
            issues.append('完全缺失或常数列' if present.empty else '常数列')
        if features['non_finite_count']:
            issues.append('存在非有限数值')
        profile = {
            'storage_type': str(series.dtype), 'inferred_type': inferred,
            'missing_count': missing, 'missing_ratio': missing / max(len(series), 1),
            'unique_count': int(len(counts)), 'is_constant': len(counts) <= 1,
            'statistical_features': features,
            # 取值频数只是统计分布。数值排序保持数值顺序，不合并低频值。
            'value_counts': {str(value): int(count) for value, count in counts.sort_index().head(50).items()} if numeric else {str(value): int(count) for value, count in counts.head(50).items()},
            'frequency_summary': {'recorded_values': min(len(counts), 50), 'total_distinct_values': len(counts), 'truncated': len(counts) > 50},
            'measurement_semantics': {
                'kind': 'unknown', 'status': 'uncertain', 'source': 'csv_inference',
                'candidates': ['count', 'ordinal', 'category', 'integer_measurement'] if integer and nonnegative else [],
                'evidence': '仅有数值和分布证据，测量含义未知。',
            },
            'causal_suitability': 'unsuitable' if present.empty else 'requires_method_check',
            'issues': issues,
        }
        if numeric and len(finite):
            profile['stats'] = {
                'mean': float(finite.mean()), 'std': float(finite.std()) if len(finite) > 1 else 0.0,
                'min': float(finite.min()), 'max': float(finite.max()),
                'q25': float(finite.quantile(.25)), 'q50': float(finite.quantile(.5)), 'q75': float(finite.quantile(.75)),
            }
        profiles[str(column)] = profile
    cells = df.shape[0] * df.shape[1]
    return {
        'n_rows': len(df), 'n_cols': len(df.columns), 'columns': [str(column) for column in df.columns],
        'data_types': {str(column): str(df[column].dtype) for column in df.columns},
        'column_profiles': profiles, 'profile_thresholds': dict(PROFILE_THRESHOLDS),
        'quality_assessment': {
            'total_missing_ratio': float(df.isna().sum().sum() / cells) if cells else 0.0,
            'constant_columns': [column for column, profile in profiles.items() if profile['is_constant']],
            'high_missing_columns': [column for column, profile in profiles.items() if profile['missing_ratio'] > .3],
        },
    }


def apply_measurement_semantics(summary: dict[str, Any], declarations: Sequence[Mapping[str, Any]], *, user_text: str) -> None:
    """只采纳可以在用户原话中定位的语义声明，不把模型推断改写成用户确认。"""
    allowed = {'count', 'ordinal', 'category', 'continuous', 'binary'}
    for declaration in declarations:
        column = declaration.get('column')
        kind = declaration.get('kind')
        evidence = declaration.get('evidence')
        if column not in summary['column_profiles'] or kind not in allowed:
            continue
        if not isinstance(evidence, str) or not evidence.strip() or evidence not in user_text or str(column) not in evidence:
            continue
        summary['column_profiles'][column]['measurement_semantics'] = {
            'kind': kind, 'status': 'user_stated', 'source': 'user_description',
            'candidates': [], 'evidence': evidence[:200],
        }
