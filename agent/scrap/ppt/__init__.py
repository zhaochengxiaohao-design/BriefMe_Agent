"""同事 graphing-ppt-charts skill 在镔鑫子包下的副本。

原始来源：from_colleague_reference/graphing-ppt-charts/
工作流：
  scripts/analyze_input.py  → 输入数据，输出 plan JSON（识别字段、推荐图表）
  scripts/build_ppt_chart.py → 输入 plan JSON，输出单页 .pptx

集成在镔鑫侧通过 ``agent.scrap.ppt_writer`` 适配（subprocess 调用上面两个脚本，
不修改同事原代码，便于将来同事更新版本时一键覆盖）。
"""
