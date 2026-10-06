using NINA.Core.Enum;
using NINA.Core.Model;
using NINA.Sequencer.Conditions;
using NINA.Sequencer.Container;
using NINA.Sequencer.Container.ExecutionStrategy;
using NINA.Sequencer.SequenceItem;
using NINA.Sequencer.Trigger;

namespace Starfront.Collab.Sequencer {

    /// <summary>
    /// Run the children once, in order, and stop.
    ///
    /// N.I.N.A.'s sequential strategy loops its children for as long as the
    /// container's conditions hold, which is right for a target and wrong for
    /// a deal: the panels dealt for tonight are the panels, and a "loop until
    /// dawn" somebody habitually drops on the container must not shoot them
    /// twice. Conditions here can only end the run early - a time or altitude
    /// limit still stops it - and the triggers of this container and of every
    /// container above it still fire around each child, so autofocus, dither
    /// and the meridian flip behave exactly as they do around a target.
    /// </summary>
    public sealed class OnceStrategy : IExecutionStrategy, ICloneable {

        public object Clone() => new OnceStrategy();

        public async Task Execute(ISequenceContainer context, IProgress<ApplicationStatus> progress, CancellationToken token) {
            context.Iterations = 0;
            Each(context, x => x.SequenceBlockInitialize(), c => c.SequenceBlockInitialize(), t => t.SequenceBlockInitialize());
            try {
                Each(context, x => x.SequenceBlockStarted(), c => c.SequenceBlockStarted(), t => t.SequenceBlockStarted());
                ISequenceItem previous = null;
                while (true) {
                    token.ThrowIfCancellationRequested();
                    var next = context.GetItemsSnapshot().FirstOrDefault(x => x.Status == SequenceEntityStatus.CREATED);
                    if (next == null) break;
                    if (!CanContinue(context, previous, next)) break;
                    await RunTriggers(context, previous, next, progress, token);
                    await next.Run(progress, token);
                    previous = next;
                    var after = context.GetItemsSnapshot().FirstOrDefault(x => x.Status == SequenceEntityStatus.CREATED);
                    await RunTriggersAfter(context, previous, after, progress, token);
                }
                context.Iterations++;
                Each(context, x => x.SequenceBlockFinished(), c => c.SequenceBlockFinished(), t => t.SequenceBlockFinished());
                foreach (var left in context.GetItemsSnapshot().Where(x => x.Status == SequenceEntityStatus.CREATED)) left.Skip();
            } finally {
                Each(context, x => x.SequenceBlockTeardown(), c => c.SequenceBlockTeardown(), t => t.SequenceBlockTeardown());
            }
        }

        private static void Each(ISequenceContainer context, Action<ISequenceItem> item, Action<ISequenceCondition> condition, Action<ISequenceTrigger> trigger) {
            foreach (var x in context.GetItemsSnapshot()) item(x);
            if (context is IConditionable conditionable) foreach (var c in conditionable.GetConditionsSnapshot()) condition(c);
            if (context is ITriggerable triggerable) foreach (var t in triggerable.GetTriggersSnapshot()) trigger(t);
        }

        private static async Task RunTriggers(ISequenceContainer container, ISequenceItem previous, ISequenceItem next, IProgress<ApplicationStatus> progress, CancellationToken token) {
            if (container is ITriggerable triggerable) await triggerable.RunTriggers(previous, next, progress, token);
            if (container?.Parent != null) await RunTriggers(container.Parent, previous, next, progress, token);
        }

        private static async Task RunTriggersAfter(ISequenceContainer container, ISequenceItem previous, ISequenceItem next, IProgress<ApplicationStatus> progress, CancellationToken token) {
            if (container is ITriggerable triggerable) await triggerable.RunTriggersAfter(previous, next, progress, token);
            if (container?.Parent != null) await RunTriggersAfter(container.Parent, previous, next, progress, token);
        }

        /// <summary>This container's conditions, then every parent's, the way the host strategy asks them.</summary>
        private static bool CanContinue(ISequenceContainer container, ISequenceItem previous, ISequenceItem next) {
            var go = true;
            if (container is IConditionable conditionable) {
                var live = conditionable.GetConditionsSnapshot().Where(x => x.Status != SequenceEntityStatus.DISABLED).ToList();
                if (live.Count > 0) go = conditionable.CheckConditions(previous, next);
            }
            if (go && container.Parent != null) go = ParentCanContinue(container.Parent, previous, next);
            return go;
        }

        private static bool ParentCanContinue(ISequenceContainer container, ISequenceItem previous, ISequenceItem next) {
            bool go;
            var conditionable = container as IConditionable;
            var live = conditionable?.GetConditionsSnapshot()?.Where(x => x.Status != SequenceEntityStatus.DISABLED).ToList();
            go = live == null || live.Count == 0 ? container.Iterations < 1 : conditionable.CheckConditions(previous, next);
            if (container.Parent != null) go = go && ParentCanContinue(container.Parent, previous, next);
            return go;
        }
    }
}
