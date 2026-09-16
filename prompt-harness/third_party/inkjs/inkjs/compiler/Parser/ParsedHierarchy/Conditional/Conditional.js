"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Conditional = void 0;
const Container_1 = require("../../../../engine/Container");
const ControlCommand_1 = require("../../../../engine/ControlCommand");
const Object_1 = require("../Object");
class Conditional extends Object_1.ParsedObject {
    constructor(initialCondition, branches) {
        super();
        this.initialCondition = initialCondition;
        this.branches = branches;
        this._reJoinTarget = null;
        this.GenerateRuntimeObject = () => {
            const container = new Container_1.Container();
            // Initial condition
            if (this.initialCondition) {
                container.AddContent(this.initialCondition.runtimeObject);
            }
            // Individual branches
            for (const branch of this.branches) {
                const branchContainer = branch.runtimeObject;
                container.AddContent(branchContainer);
            }
            // If it's a switch-like conditional, each branch
            // will have a "duplicate" operation for the original
            // switched value. If there's no final else clause
            // and we fall all the way through, we need to clean up.
            // (An else clause doesn't dup but it *does* pop)
            if (this.initialCondition !== null &&
                this.branches[0].ownExpression !== null &&
                !this.branches[this.branches.length - 1].isElse) {
                container.AddContent(ControlCommand_1.ControlCommand.PopEvaluatedValue());
            }
            // Target for branches to rejoin to
            this._reJoinTarget = ControlCommand_1.ControlCommand.NoOp();
            container.AddContent(this._reJoinTarget);
            return container;
        };
        if (this.initialCondition) {
            this.AddContent(this.initialCondition);
        }
        if (this.branches !== null) {
            this.AddContent(this.branches);
        }
    }
    get typeName() {
        return "Conditional";
    }
    ResolveReferences(context) {
        const pathToReJoin = this._reJoinTarget.path;
        for (const branch of this.branches) {
            if (!branch.returnDivert) {
                throw new Error();
            }
            branch.returnDivert.targetPath = pathToReJoin;
        }
        super.ResolveReferences(context);
    }
}
exports.Conditional = Conditional;
//# sourceMappingURL=Conditional.js.map