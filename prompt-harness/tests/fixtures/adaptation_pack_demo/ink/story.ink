// 演示 pack 的 ink 骨架（改编层主出口的人类可读形态；story.ink.json 为编译产物，由编译器生成）
VAR flags_read_card = false
VAR flags_searched_cabinet = false
VAR flags_saw_white_figure = false

-> n001

=== n001 ===
深夜的旧教室里，粉笔灰在路灯光柱中缓缓下沉，每个座位上都坐着一个纸人。
这是哪儿？我明明记得刚交完最后一科的卷子……
~ stage("paper_figures_turn")
陈守念：谁在看我？
-> c001

=== c001 ===
* [先看那张病人卡] ~ flags_read_card = true -> n002
* [先去翻储物柜] ~ flags_searched_cabinet = true -> n002

=== n002 ===
走廊尽头，一张病人卡从课桌缝里滑出来，正面朝上。
林老师：你终于把它捡起来了。
陈守念：这名字……是我自己的。
~ stage("light_flicker")
{ flags_read_card: -> end_a }
-> end_b

=== end_a ===
接受真相
-> END

=== end_b ===
拒绝离开
-> END
