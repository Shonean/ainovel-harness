"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Flow = void 0;
const CallStack_1 = require("./CallStack");
const JsonSerialisation_1 = require("./JsonSerialisation");
const NullException_1 = require("./NullException");
class Flow {
    constructor() {
        let name = arguments[0];
        let story = arguments[1];
        this.name = name;
        this.callStack = new CallStack_1.CallStack(story);
        if (arguments[2]) {
            let jObject = arguments[2];
            this.callStack.SetJsonToken(jObject["callstack"], story);
            this.outputStream = JsonSerialisation_1.JsonSerialisation.JArrayToRuntimeObjList(jObject["outputStream"]);
            this.currentChoices = JsonSerialisation_1.JsonSerialisation.JArrayToRuntimeObjList(jObject["currentChoices"]);
            let jChoiceThreadsObj = jObject["choiceThreads"];
            if (typeof jChoiceThreadsObj !== "undefined") {
                this.LoadFlowChoiceThreads(jChoiceThreadsObj, story);
            }
        }
        else {
            this.outputStream = [];
            this.currentChoices = [];
        }
    }
    WriteJson(writer) {
        writer.WriteObjectStart();
        writer.WriteProperty("callstack", (w) => this.callStack.WriteJson(w));
        writer.WriteProperty("outputStream", (w) => JsonSerialisation_1.JsonSerialisation.WriteListRuntimeObjs(w, this.outputStream));
        let hasChoiceThreads = false;
        for (let c of this.currentChoices) {
            if (c.threadAtGeneration === null)
                return (0, NullException_1.throwNullException)("c.threadAtGeneration");
            c.originalThreadIndex = c.threadAtGeneration.threadIndex;
            if (this.callStack.ThreadWithIndex(c.originalThreadIndex) === null) {
                if (!hasChoiceThreads) {
                    hasChoiceThreads = true;
                    writer.WritePropertyStart("choiceThreads");
                    writer.WriteObjectStart();
                }
                writer.WritePropertyStart(c.originalThreadIndex);
                c.threadAtGeneration.WriteJson(writer);
                writer.WritePropertyEnd();
            }
        }
        if (hasChoiceThreads) {
            writer.WriteObjectEnd();
            writer.WritePropertyEnd();
        }
        writer.WriteProperty("currentChoices", (w) => {
            w.WriteArrayStart();
            for (let c of this.currentChoices) {
                JsonSerialisation_1.JsonSerialisation.WriteChoice(w, c);
            }
            w.WriteArrayEnd();
        });
        writer.WriteObjectEnd();
    }
    LoadFlowChoiceThreads(jChoiceThreads, story) {
        for (let choice of this.currentChoices) {
            let foundActiveThread = this.callStack.ThreadWithIndex(choice.originalThreadIndex);
            if (foundActiveThread !== null) {
                choice.threadAtGeneration = foundActiveThread.Copy();
            }
            else {
                let jSavedChoiceThread = jChoiceThreads[`${choice.originalThreadIndex}`];
                choice.threadAtGeneration = new CallStack_1.CallStack.Thread(jSavedChoiceThread, story);
            }
        }
    }
}
exports.Flow = Flow;
//# sourceMappingURL=Flow.js.map