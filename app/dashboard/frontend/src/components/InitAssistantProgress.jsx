import React from 'react'

const stages = [
  { id: 'name', label: '书名', icon: '' },
  { id: 'genre', label: '类型', icon: '' },
  { id: 'settings', label: '基本设定', icon: '' },
  { id: 'elements', label: '元素生成', icon: '' },
  { id: 'arc', label: '开篇情节', icon: '' },
]

/**
 * 初始化创作助手进度条组件。
 * 显示当前所处阶段、已完成进度和快捷操作按钮。
 *
 * Props:
 *  - currentStage: string（当前阶段 id）
 *  - completedItems: number（已完成阶段数）
 *  - totalItems: number（总阶段数）
 *  - onQuickAction: (stageId) => void（点击阶段触发）
 */
export default function InitAssistantProgress({
  currentStage = '',
  completedItems = 0,
  totalItems = 5,
  onQuickAction,
}) {
  return (
    <div className="init-progress">
      <div className="init-stage-indicators">
        {stages.map((stage, index) => {
          const isCompleted = completedItems > index
          const isActive = currentStage === stage.id
          return (
            <div
              key={stage.id}
              className={`init-stage ${isCompleted ? 'completed' : ''} ${isActive ? 'active' : ''}`}
              onClick={() => onQuickAction?.(stage.id)}
              title={stage.label}
            >
              <span className="init-stage-label">{stage.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
