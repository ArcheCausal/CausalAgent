def validate_analysis(analysis_params: dict, target: str = None, treatment: str = None) -> tuple[int, list[str],list[str]]:
    """
    基于增强的数据摘要进行因果分析就绪性验证。
    
    Args:
        analysis_params: get_data_summary返回的完整数据摘要
        target: 目标变量名
        treatment: 处理变量名
        
    Returns:
        tuple[bool, list[str],list[str]]: (是否通过验证, 问题列表, 建议列表)
        0表示通过验证，1表示缺少目标变量或处理变量，但是不影响分析，2错误，需要立即返回
    """
    issues = []
    recommends = []
    columns = analysis_params.get("columns", [])
    column_profiles = analysis_params.get("column_profiles", {})
    quality_assessment = analysis_params.get("quality_assessment", {})
    

    if target == "None":
        target = None
    if treatment == "None":
        treatment = None
    
    # 缺少目标变量和处理变量不需要报错
    if not target or not treatment:
        # 因果发现不要求指定目标或处理变量；始终继续完成通用质量检查，
        # 不在这里提前返回。
        recommends.append("未指定目标变量(target)或处理变量(treatment)，仍可按因果发现继续")

    parameter_errors = []
    if target != None and target not in columns:
        parameter_errors.append(f"目标变量 '{target}' 不存在于数据列中")
    if treatment != None and treatment not in columns:
        parameter_errors.append(f"处理变量 '{treatment}' 不存在于数据列中")
    if target != None and treatment != None and target == treatment:
        parameter_errors.append("目标变量与处理变量不能相同")
    
    if parameter_errors:
        issues.extend(parameter_errors)
        return (2, issues, recommends) # 参数名错误，立即返回

    # 2. 指定变量的数据质量检查 
    for var_name, var_type in [("目标变量", target), ("处理变量", treatment)]:
        if var_type in column_profiles:
            profile = column_profiles[var_type]
            
            # 检查是否为不适用的列类型
            if profile.get('causal_suitability') == 'unsuitable':
                issues.append(f"{var_name} '{var_type}' 完全缺失，无法用于分析")
            elif profile.get('causal_suitability') == 'poor':
                if profile.get('possible_id', False):
                    issues.append(f"{var_name} '{var_type}' 疑似ID列，不适合直接用于因果分析")
                elif profile.get('inferred_type') == 'high_cardinality_categorical':
                    issues.append(f"{var_name} '{var_type}' 为高基数分类变量，建议先进行编码或分组处理")
            
            # 检查缺失率
            if profile.get('missing_ratio', 0) > 0.3:
                issues.append(f"{var_name} '{var_type}' 缺失率过高({profile['missing_ratio']:.1%})，建议先处理缺失值")
            
            # 检查是否为常数列
            if profile.get('is_constant', False):
                issues.append(f"{var_name} '{var_type}' 为常数列或唯一值过少，无法进行有效分析")

    # 整体数据质量与规模检查 
    n_rows = analysis_params.get('n_rows', 0)
    if n_rows < 50:
        recommends.append(f"样本量过小(n={n_rows})，建议至少有50个观测值以确保分析的稳健性")
    
    total_missing_ratio = quality_assessment.get('total_missing_ratio', 0)
    if total_missing_ratio > 0.2:
        recommends.append(f"整体缺失率过高({total_missing_ratio:.1%})，建议先进行数据清洗")
    
    constant_columns = quality_assessment.get('constant_columns', [])
    # 排除已经是target或treatment的常数列，避免重复报错
    other_constant_columns = [c for c in constant_columns if c != target and c != treatment]
    if len(other_constant_columns) > 0:
        recommends.append(f"存在常数列: {', '.join(other_constant_columns[:3])}{'等' if len(other_constant_columns) > 3 else ''}将不会进入因果分析")

    # 4. 生成建议 (非阻断性问题)
    datetime_columns = [col for col, profile in column_profiles.items() 
                       if profile.get('inferred_type') == 'datetime']
    if datetime_columns and len(datetime_columns) > 0:
        recommends.append(f"数据中包含时间变量: {', '.join(datetime_columns[:3])}{'等' if len(datetime_columns) > 3 else ''}，本次分析将作为普通变量处理。如需专门的时间序列因果分析，请告知。")
    
    # 数据处理由独立子图完成，这里只生成非阻断的数据质量说明。
    # 只有参数事实错误返回 2。返回值 1 表示“未指定目标或
    # 处理变量但可继续分析”。
    return (1 if issues else 0, issues, recommends)
