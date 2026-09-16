"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Expression = void 0;
const Container_1 = require("../../../../engine/Container");
const ControlCommand_1 = require("../../../../engine/ControlCommand");
const Object_1 = require("../Object");
class Expression extends Object_1.ParsedObject {
    constructor() {
        super(...arguments);
        this._prototypeRuntimeConstantExpression = null;
        this.outputWhenComplete = false;
        this.GenerateRuntimeObject = () => {
            const container = new Container_1.Container();
            // Tell Runtime to start evaluating the following content as an expression
            container.AddContent(ControlCommand_1.ControlCommand.EvalStart());
            this.GenerateIntoContainer(container);
            // Tell Runtime to output the result of the expression evaluation to the output stream
            if (this.outputWhenComplete) {
                container.AddContent(ControlCommand_1.ControlCommand.EvalOutput());
            }
            // Tell Runtime to stop evaluating the content as an expression
            container.AddContent(ControlCommand_1.ControlCommand.EvalEnd());
            return container;
        };
        // When generating the value of a constant expression,
        // we can't just keep generating the same constant expression into
        // different places where the constant value is referenced, since then
        // the same runtime objects would be used in multiple places, which
        // is impossible since each runtime object should have one parent.
        // Instead, we generate a prototype of the runtime object(s), then
        // copy them each time they're used.
        this.GenerateConstantIntoContainer = (container) => {
            if (this._prototypeRuntimeConstantExpression === null) {
                this._prototypeRuntimeConstantExpression = new Container_1.Container();
                this.GenerateIntoContainer(this._prototypeRuntimeConstantExpression);
            }
            for (const runtimeObj of this._prototypeRuntimeConstantExpression.content) {
                const copy = runtimeObj.Copy();
                if (copy) {
                    container.AddContent(copy);
                }
            }
        };
        this.toString = () => "No string value in JavaScript.";
    }
    get typeName() {
        return "Expression";
    }
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    Equals(obj) {
        return false;
    }
}
exports.Expression = Expression;
//# sourceMappingURL=Expression.js.map