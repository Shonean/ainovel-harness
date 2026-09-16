import { Story } from "./Parser/ParsedHierarchy/Story";
export interface Stats {
    words: number;
    knots: number;
    stitches: number;
    functions: number;
    choices: number;
    gathers: number;
    diverts: number;
}
export declare function GenerateStoryStats(story: Story): Stats;
