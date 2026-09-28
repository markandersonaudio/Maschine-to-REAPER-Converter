#pragma once

#include <JuceHeader.h>

//==============================================================================
class MaschineEQAudioProcessor : public juce::AudioProcessor
{
public:
    MaschineEQAudioProcessor();
    ~MaschineEQAudioProcessor() override;

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;

#ifndef JucePlugin_PreferredChannelConfigurations
    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;
#endif

    void processBlock(
        juce::AudioBuffer<float>&,
        juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override;

    const juce::String getName() const override;

    bool acceptsMidi() const override;
    bool producesMidi() const override;
    bool isMidiEffect() const override;

    double getTailLengthSeconds() const override;

    int getNumPrograms() override;
    int getCurrentProgram() override;

    void setCurrentProgram(int index) override;

    const juce::String getProgramName(int index) override;

    void changeProgramName(
        int index,
        const juce::String& newName) override;

    void getStateInformation(
        juce::MemoryBlock& destData) override;

    void setStateInformation(
        const void* data,
        int sizeInBytes) override;

    juce::AudioProcessorValueTreeState parameters;

    static juce::AudioProcessorValueTreeState::ParameterLayout
        createParameterLayout();

private:
    using Filter =
        juce::dsp::IIR::Filter<float>;

    using Coefficients =
        juce::dsp::IIR::Coefficients<float>;

    using StereoFilter =
        juce::dsp::ProcessorDuplicator<
            Filter,
            Coefficients>;

    StereoFilter lowShelf;
    StereoFilter lowMidPeak;
    StereoFilter highMidPeak;
    StereoFilter highShelf;

    juce::dsp::DelayLine<
        float,
        juce::dsp::DelayLineInterpolationTypes::None
    > eqDelay { 1 };

    juce::dsp::Gain<float> outputGain;

    double currentSampleRate = 44100.0;

    void updateFilters();

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(
        MaschineEQAudioProcessor)
};
