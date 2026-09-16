"use strict";
// import { FlowBase } from './FlowBase';
Object.defineProperty(exports, "__esModule", { value: true });
exports.ClosestFlowBase = void 0;
function ClosestFlowBase(obj) {
    let ancestor = obj.parent;
    while (ancestor) {
        if (ancestor.hasOwnProperty("iamFlowbase") && ancestor.iamFlowbase()) {
            return ancestor;
        }
        ancestor = ancestor.parent;
    }
    return null;
}
exports.ClosestFlowBase = ClosestFlowBase;
//# sourceMappingURL=ClosestFlowBase.js.map