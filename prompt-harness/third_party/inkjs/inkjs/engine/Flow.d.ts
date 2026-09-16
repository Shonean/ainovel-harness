import { CallStack } from "./CallStack";
import { Choice } from "./Choice";
import { InkObject } from "./Object";
import { SimpleJson } from "./SimpleJson";
import { Story } from "./Story";
export declare class Flow {
    name: string;
    callStack: CallStack;
    outputStream: InkObject[];
    currentChoices: Choice[];
    constructor(name: String, story: Story);
    constructor(name: String, story: Story, jObject: Record<string, any>);
    WriteJson(writer: SimpleJson.Writer): undefined;
    LoadFlowChoiceThreads(jChoiceThreads: Record<string, any>, story: Story): void;
}
