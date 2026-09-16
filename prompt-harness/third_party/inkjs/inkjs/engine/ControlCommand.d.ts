import { InkObject } from "./Object";
export declare class ControlCommand extends InkObject {
    private _commandType;
    get commandType(): ControlCommand.CommandType;
    constructor(commandType?: ControlCommand.CommandType);
    Copy(): ControlCommand;
    static EvalStart(): ControlCommand;
    static EvalOutput(): ControlCommand;
    static EvalEnd(): ControlCommand;
    static Duplicate(): ControlCommand;
    static PopEvaluatedValue(): ControlCommand;
    static PopFunction(): ControlCommand;
    static PopTunnel(): ControlCommand;
    static BeginString(): ControlCommand;
    static EndString(): ControlCommand;
    static NoOp(): ControlCommand;
    static ChoiceCount(): ControlCommand;
    static Turns(): ControlCommand;
    static TurnsSince(): ControlCommand;
    static ReadCount(): ControlCommand;
    static Random(): ControlCommand;
    static SeedRandom(): ControlCommand;
    static VisitIndex(): ControlCommand;
    static SequenceShuffleIndex(): ControlCommand;
    static StartThread(): ControlCommand;
    static Done(): ControlCommand;
    static End(): ControlCommand;
    static ListFromInt(): ControlCommand;
    static ListRange(): ControlCommand;
    static ListRandom(): ControlCommand;
    static BeginTag(): ControlCommand;
    static EndTag(): ControlCommand;
    toString(): string;
}
export declare namespace ControlCommand {
    enum CommandType {
        NotSet = -1,
        EvalStart = 0,// 0
        EvalOutput = 1,// 1
        EvalEnd = 2,// 2
        Duplicate = 3,// 3
        PopEvaluatedValue = 4,// 4
        PopFunction = 5,// 5
        PopTunnel = 6,// 6
        BeginString = 7,// 7
        EndString = 8,// 8
        NoOp = 9,// 9
        ChoiceCount = 10,// 10
        Turns = 11,// 11
        TurnsSince = 12,// 12
        ReadCount = 13,// 13
        Random = 14,// 14
        SeedRandom = 15,// 15
        VisitIndex = 16,// 16
        SequenceShuffleIndex = 17,// 17
        StartThread = 18,// 18
        Done = 19,// 19
        End = 20,// 20
        ListFromInt = 21,// 21
        ListRange = 22,// 22
        ListRandom = 23,// 23
        BeginTag = 24,// 24
        EndTag = 25,// 25
        TOTAL_VALUES = 26
    }
}
