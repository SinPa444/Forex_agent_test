#!/usr/bin/env python3
"""
run.py
======
Unified Pipeline Entry Point for the Forex AI Fundamental Analysis Platform.

Usage:
  python run.py --mode news --llm-provider arvan --max-items 15
  python run.py --mode events --source db --max-events 5
  python run.py --mode speakers --currencies USD EUR
  python run.py --mode all --llm-provider arvan
  
Modes:
  news    : Run RSS news analysis pipeline (e2e_news_pipeline)
  events  : Run economic event analysis pipeline (e2e_pipeline)
  speakers: Run speaker statement analysis pipeline (e2e_speaker_pipeline)
  all     : Run events, news, and speakers sequentially
"""

import argparse
import logging
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("run_pipeline")

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified Forex AI Pipeline Entry Point",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--mode",
        choices=["news", "events", "speakers", "all"],
        required=True,
        help="Which pipeline to run."
    )
    
    # ما بقی آرگومان‌ها را می‌گیریم تا به پایپ‌لاین مربوطه پاس بدهیم
    known_args, remaining_argv = parser.parse_known_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    
    start_time = time.perf_counter()
    exit_code = 0
    
    logger.info("═" * 76)
    logger.info(f"STARTING UNIFIED PIPELINE | MODE={known_args.mode.upper()}")
    logger.info("═" * 76)
    
    if known_args.mode in ["events", "all"]:
        logger.info(">>> Running EVENTS Pipeline...")
        try:
            from agents.fundamental.e2e_pipeline import main as events_main
            ret = events_main(remaining_argv if remaining_argv else None)
            if ret != 0:
                logger.error(f"EVENTS Pipeline exited with code {ret}")
                exit_code = ret
        except Exception as exc:
            logger.critical(f"EVENTS Pipeline failed with exception: {exc}", exc_info=True)
            exit_code = 1

    if known_args.mode in ["news", "all"]:
        logger.info(">>> Running NEWS Pipeline...")
        try:
            from agents.fundamental.e2e_news_pipeline import main as news_main
            # پاس دادن آرگومان‌های باقی‌مانده (مثلاً --max-items 10) به پایپ‌لاین اخبار
            ret = news_main(remaining_argv if remaining_argv else None)
            if ret != 0:
                logger.error(f"NEWS Pipeline exited with code {ret}")
                exit_code = ret
        except Exception as exc:
            logger.critical(f"NEWS Pipeline failed with exception: {exc}", exc_info=True)
            exit_code = 1

    if known_args.mode in ["speakers", "all"]:
        logger.info(">>> Running SPEAKERS Pipeline...")
        try:
            from agents.fundamental.e2e_speaker_pipeline import main as speakers_main
            ret = speakers_main(remaining_argv if remaining_argv else None)
            if ret != 0:
                logger.error(f"SPEAKERS Pipeline exited with code {ret}")
                exit_code = ret
        except Exception as exc:
            logger.critical(f"SPEAKERS Pipeline failed with exception: {exc}", exc_info=True)
            exit_code = 1

    end_time = time.perf_counter()
    duration = end_time - start_time
    
    logger.info("═" * 76)
    logger.info(f"UNIFIED PIPELINE COMPLETE | MODE={known_args.mode.upper()} | Duration={duration:.2f}s | Exit={exit_code}")
    logger.info("═" * 76)
    
    return exit_code

if __name__ == "__main__":
    sys.exit(main())