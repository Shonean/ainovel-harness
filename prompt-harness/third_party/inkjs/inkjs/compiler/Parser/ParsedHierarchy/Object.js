"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ParsedObject = void 0;
const TypeAssertion_1 = require("../../../engine/TypeAssertion");
class ParsedObject {
    constructor() {
        this._alreadyHadError = false;
        this._alreadyHadWarning = false;
        this._debugMetadata = null;
        this._runtimeObject = null;
        this.content = [];
        this.parent = null;
        this.GetType = () => this.typeName;
        /*
        get descriptionOfScope(): string {
          const locationNames: string[] = [];
      
          let ancestor: ParsedObject | null = this;
          while (ancestor) {
            var ancestorFlow = ancestor as FlowBase;
            if (ancestorFlow && ancestorFlow.name != null) {
              locationNames.push(`'${ancestorFlow.name}'`);
            }
            ancestor = ancestor.parent;
          }
      
          let scopeSB = '';
          if (locationNames.length > 0) {
            const locationsListStr = locationNames.join(', ');
            scopeSB += `${locationsListStr} and`;
          }
      
          scopeSB += 'at top scope';
      
          return scopeSB;
        }
      */
        // Return the object so that method can be chained easily
        this.AddContent = (subContent) => {
            if (this.content === null) {
                this.content = [];
            }
            const sub = Array.isArray(subContent) ? subContent : [subContent];
            // Make resilient to content not existing, which can happen
            // in the case of parse errors where we've already reported
            // an error but still want a valid structure so we can
            // carry on parsing.
            for (const ss of sub) {
                if (ss.hasOwnProperty("parent")) {
                    ss.parent = this;
                }
                this.content.push(ss);
            }
            if (Array.isArray(subContent)) {
                return;
            }
            else {
                return subContent;
            }
        };
        this.InsertContent = (index, subContent) => {
            if (this.content === null) {
                this.content = [];
            }
            subContent.parent = this;
            this.content.splice(index, 0, subContent);
            return subContent;
        };
        this.Find = (type) => (queryFunc = null) => {
            let tObj = (0, TypeAssertion_1.asOrNull)(this, type);
            if (tObj !== null && (queryFunc === null || queryFunc(tObj) === true)) {
                return tObj;
            }
            if (this.content === null) {
                return null;
            }
            for (const obj of this.content) {
                let nestedResult = obj.Find && obj.Find(type)(queryFunc);
                if (nestedResult) {
                    return nestedResult;
                }
            }
            return null;
        };
        this.FindAll = (type) => (queryFunc, foundSoFar) => {
            const found = Array.isArray(foundSoFar) ? foundSoFar : [];
            const tObj = (0, TypeAssertion_1.asOrNull)(this, type);
            if (tObj !== null && (!queryFunc || queryFunc(tObj) === true)) {
                found.push(tObj);
            }
            if (this.content === null) {
                return [];
            }
            for (const obj of this.content) {
                obj.FindAll && obj.FindAll(type)(queryFunc, found);
            }
            return found;
        };
        this.Warning = (message, source = null) => {
            this.Error(message, source, true);
        };
    }
    get debugMetadata() {
        if (this._debugMetadata === null && this.parent) {
            return this.parent.debugMetadata;
        }
        return this._debugMetadata;
    }
    set debugMetadata(value) {
        this._debugMetadata = value;
    }
    get hasOwnDebugMetadata() {
        return Boolean(this.debugMetadata);
    }
    get typeName() {
        return "ParsedObject";
    }
    get story() {
        let ancestor = this;
        while (ancestor.parent) {
            ancestor = ancestor.parent;
        }
        return ancestor;
    }
    get runtimeObject() {
        if (!this._runtimeObject) {
            this._runtimeObject = this.GenerateRuntimeObject();
            if (this._runtimeObject) {
                this._runtimeObject.debugMetadata = this.debugMetadata;
            }
        }
        return this._runtimeObject;
    }
    set runtimeObject(value) {
        this._runtimeObject = value;
    }
    get runtimePath() {
        if (!this.runtimeObject.path) {
            throw new Error();
        }
        return this.runtimeObject.path;
    }
    // When counting visits and turns since, different object
    // types may have different containers that needs to be counted.
    // For most it'll just be the object's main runtime object,
    // but for e.g. choices, it'll be the target container.
    get containerForCounting() {
        return this.runtimeObject;
    }
    get ancestry() {
        let result = [];
        let ancestor = this.parent;
        while (ancestor) {
            result.push(ancestor);
            ancestor = ancestor.parent;
        }
        result = result.reverse();
        return result;
    }
    ResolveReferences(context) {
        if (this.content !== null) {
            for (const obj of this.content) {
                obj.ResolveReferences(context);
            }
        }
    }
    Error(message, source = null, isWarning = false) {
        if (source === null) {
            source = this;
        }
        // Only allow a single parsed object to have a single error *directly* associated with it
        if ((source._alreadyHadError && !isWarning) ||
            (source._alreadyHadWarning && isWarning)) {
            return;
        }
        if (this.parent) {
            this.parent.Error(message, source, isWarning);
        }
        else {
            throw new Error(`No parent object to send error to: ${message}`);
        }
        if (isWarning) {
            source._alreadyHadWarning = true;
        }
        else {
            source._alreadyHadError = true;
        }
    }
}
exports.ParsedObject = ParsedObject;
//# sourceMappingURL=Object.js.map