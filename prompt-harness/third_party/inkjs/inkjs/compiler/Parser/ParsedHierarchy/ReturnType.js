"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ReturnType = void 0;
const Object_1 = require("./Object");
const Container_1 = require("../../../engine/Container");
const ControlCommand_1 = require("../../../engine/ControlCommand");
const Void_1 = require("../../../engine/Void");
class ReturnType extends Object_1.ParsedObject {
    constructor(returnedExpression = null) {
        super();
        this.returnedExpression = null;
        this.GenerateRuntimeObject = () => {
            const container = new Container_1.Container();
            if (this.returnedExpression) {
                // Evaluate expression
                container.AddContent(this.returnedExpression.runtimeObject);
            }
            else {
                // Return Runtime.Void when there's no expression to evaluate
                // (This evaluation will just add the Void object to the evaluation stack)
                container.AddContent(ControlCommand_1.ControlCommand.EvalStart());
                container.AddContent(new Void_1.Void());
                container.AddContent(ControlCommand_1.ControlCommand.EvalEnd());
            }
            // Then pop the call stack
            // (the evaluated expression will leave the return value on the evaluation stack)
            container.AddContent(ControlCommand_1.ControlCommand.PopFunction());
            return container;
        };
        if (returnedExpression) {
            this.returnedExpression = this.AddContent(returnedExpression);
        }
    }
    get typeName() {
        return "ReturnType";
    }
}
exports.ReturnType = ReturnType;
//# sourceMappingURL=ReturnType.js.map