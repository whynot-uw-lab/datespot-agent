import type { PlaceResult } from "./contracts";

// @ts-expect-error analyzed 결과에는 finalScore가 필요함
const analyzedWithoutFinalScore: PlaceResult = {
  status: "analyzed",
  name: "점수 누락 장소",
};

// @ts-expect-error failed 결과에는 failureReason이 필요함
const failedWithoutReason: PlaceResult = {
  status: "failed",
  name: "실패 사유 누락 장소",
};

void analyzedWithoutFinalScore;
void failedWithoutReason;
