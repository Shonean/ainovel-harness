"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.TunnelOnwards = void 0;
const Container_1 = require("../../../engine/Container");
const ControlCommand_1 = require("../../../engine/ControlCommand");
const Divert_1 = require("../../../engine/Divert");
const Value_1 = require("../../../engine/Value");
const Object_1 = require("./Object");
const Void_1 = require("../../../engine/Void");
const TypeAssertion_1 = require("../../../engine/TypeAssertion");
const VariableReference_1 = require("../../../engine/VariableReference");
class TunnelOnwards extends Object_1.ParsedObject {
    constructor() {
        super(...arguments);
        this._overrideDivertTarget = null;
        this._divertAfter = null;
        this.GenerateRuntimeObject = () => {
            const container = new Container_1.Container();
            // Set override path for tunnel onwards (or nothing)
            container.AddContent(ControlCommand_1.ControlCommand.EvalStart());
            if (this.divertAfter) {
                // Generate runtime object's generated code and steal the arguments runtime code
                const returnRuntimeObj = this.divertAfter.GenerateRuntimeObject();
                const returnRuntimeContainer = returnRuntimeObj;
                if (returnRuntimeContainer) {
                    // Steal all code for generating arguments from the divert
                    const args = this.divertAfter.args;
                    if (args !== null && args.length > 0) {
                        // Steal everything betwen eval start and eval end
                        let evalStart = -1;
                        let evalEnd = -1;
                        for (let ii = 0; ii < returnRuntimeContainer.content.length; ii += 1) {
                            const cmd = returnRuntimeContainer.content[ii];
                            if (cmd) {
                                if (evalStart == -1 &&
                                    cmd.commandType === ControlCommand_1.ControlCommand.CommandType.EvalStart) {
                                    evalStart = ii;
                                }
                                else if (cmd.commandType === ControlCommand_1.ControlCommand.CommandType.EvalEnd) {
                                    evalEnd = ii;
                                }
                            }
                        }
                        for (let ii = evalStart + 1; ii < evalEnd; ii += 1) {
                            const obj = returnRuntimeContainer.content[ii];
                            obj.parent = null; // prevent error of being moved between owners
                            container.AddContent(returnRuntimeContainer.content[ii]);
                        }
                    }
                }
                // Supply the divert target for the tunnel onwards target, either variable or more commonly, the explicit name
                // var returnDivertObj = returnRuntimeObj as Runtime.Divert;
                let returnDivertObj = (0, TypeAssertion_1.asOrNull)(returnRuntimeObj, Divert_1.Divert);
                if (returnDivertObj != null && returnDivertObj.hasVariableTarget) {
                    let runtimeVarRef = new VariableReference_1.VariableReference(returnDivertObj.variableDivertName);
                    container.AddContent(runtimeVarRef);
                }
                else {
                    this._overrideDivertTarget = new Value_1.DivertTargetValue();
                    container.AddContent(this._overrideDivertTarget);
                }
            }
            else {
                // No divert after tunnel onwards
                container.AddContent(new Void_1.Void());
            }
            container.AddContent(ControlCommand_1.ControlCommand.EvalEnd());
            container.AddContent(ControlCommand_1.ControlCommand.PopTunnel());
            return container;
        };
        this.toString = () => {
            return ` -> ${this._divertAfter}`;
        };
    }
    get divertAfter() {
        return this._divertAfter;
    }
    set divertAfter(value) {
        this._divertAfter = value;
        if (this._divertAfter) {
            this.AddContent(this._divertAfter);
        }
    }
    get typeName() {
        return "TunnelOnwards";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        if (this.divertAfter && this.divertAfter.targetContent) {
            this._overrideDivertTarget.targetPath =
                this.divertAfter.targetContent.runtimePath;
        }
    }
}
exports.TunnelOnwards = TunnelOnwards;
//# sourceMappingURL=TunnelOnwards.js.map